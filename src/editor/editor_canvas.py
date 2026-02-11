"""
Editor canvas widget for UbShot.

The EditorCanvas is the main drawing area that displays:
- The base screenshot image
- All annotation objects on top
- Selection handles for selected annotations

Supports:
- Zoom (Ctrl+wheel, keyboard shortcuts)
- Pan (Space+drag)
- Tool-based interaction (delegated to active tool)
- Undo/Redo via QUndoStack
"""

from typing import List, Optional

from PySide6.QtCore import (
    QPointF,
    QRect,
    QRectF,
    Qt,
    Signal,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
    QUndoCommand,
    QUndoStack,
    QWheelEvent,
)
from PySide6.QtWidgets import QLineEdit, QWidget

from src.editor.annotations import AnnotationBase, AnnotationStyle, AnnotationType, TextAnnotation
from src.editor.tools import PointerTool, ToolBase, ToolType, create_tool
from src.services.logging_service import get_logger


# ─── Undo Commands ────────────────────────────────────────────────────────────

class AddAnnotationCommand(QUndoCommand):
    """Command for adding an annotation."""
    
    def __init__(self, canvas: "EditorCanvas", annotation: AnnotationBase) -> None:
        super().__init__("Add Annotation")
        self._canvas = canvas
        self._annotation = annotation
    
    def redo(self) -> None:
        self._canvas._annotations.append(self._annotation)
        self._canvas.update()
    
    def undo(self) -> None:
        if self._annotation in self._canvas._annotations:
            self._canvas._annotations.remove(self._annotation)
        if self._canvas._selected_annotation == self._annotation:
            self._canvas._selected_annotation = None
        self._canvas.update()


class DeleteAnnotationCommand(QUndoCommand):
    """Command for deleting an annotation."""
    
    def __init__(self, canvas: "EditorCanvas", annotation: AnnotationBase) -> None:
        super().__init__("Delete Annotation")
        self._canvas = canvas
        self._annotation = annotation
        self._index = -1
    
    def redo(self) -> None:
        if self._annotation in self._canvas._annotations:
            self._index = self._canvas._annotations.index(self._annotation)
            self._canvas._annotations.remove(self._annotation)
        if self._canvas._selected_annotation == self._annotation:
            self._canvas._selected_annotation = None
        self._canvas.update()
    
    def undo(self) -> None:
        if self._index >= 0:
            self._canvas._annotations.insert(self._index, self._annotation)
        else:
            self._canvas._annotations.append(self._annotation)
        self._canvas.update()


class MoveAnnotationCommand(QUndoCommand):
    """Command for moving/resizing an annotation."""
    
    def __init__(
        self,
        canvas: "EditorCanvas",
        annotation: AnnotationBase,
        old_state: AnnotationBase,
        new_state: AnnotationBase
    ) -> None:
        super().__init__("Move Annotation")
        self._canvas = canvas
        self._annotation = annotation
        self._old_state = old_state
        self._new_state = new_state
    
    def redo(self) -> None:
        self._apply_state(self._new_state)
    
    def undo(self) -> None:
        self._apply_state(self._old_state)
    
    def _apply_state(self, state: AnnotationBase) -> None:
        # Copy properties from state to annotation
        rect = state.bounding_rect
        if hasattr(self._annotation, '_rect'):
            self._annotation._rect = QRectF(rect)
        elif hasattr(self._annotation, '_start') and hasattr(self._annotation, '_end'):
            # Arrow
            self._annotation._start = QPointF(state._start)
            self._annotation._end = QPointF(state._end)
        elif hasattr(self._annotation, '_position'):
            # Text
            self._annotation._position = QPointF(state._position)
        self._canvas.update()


class CropCommand(QUndoCommand):
    """Command for cropping the image."""
    
    def __init__(
        self,
        canvas: "EditorCanvas",
        old_image: QImage,
        new_image: QImage,
        old_annotations: List[AnnotationBase]
    ) -> None:
        super().__init__("Crop Image")
        self._canvas = canvas
        self._old_image = old_image
        self._new_image = new_image
        self._old_annotations = old_annotations
    
    def redo(self) -> None:
        self._canvas._image = self._new_image
        self._canvas._annotations.clear()  # TODO: Adjust annotations instead
        self._canvas._selected_annotation = None
        self._canvas.zoom_to_fit()
        self._canvas.image_changed.emit()
        self._canvas.update()
    
    def undo(self) -> None:
        self._canvas._image = self._old_image
        self._canvas._annotations = list(self._old_annotations)
        self._canvas.zoom_to_fit()
        self._canvas.image_changed.emit()
        self._canvas.update()


class InpaintCommand(QUndoCommand):
    """Command for content-aware inpainting (eraser tool)."""
    
    def __init__(
        self,
        canvas: "EditorCanvas",
        old_image: QImage,
        new_image: QImage
    ) -> None:
        super().__init__("Inpaint")
        self._canvas = canvas
        self._old_image = old_image
        self._new_image = new_image
    
    def redo(self) -> None:
        self._canvas._image = self._new_image
        self._canvas.image_changed.emit()
        self._canvas.update()
    
    def undo(self) -> None:
        self._canvas._image = self._old_image
        self._canvas.image_changed.emit()
        self._canvas.update()


# ─── Editor Canvas ────────────────────────────────────────────────────────────

class EditorCanvas(QWidget):
    """
    Main canvas widget for displaying and editing screenshots.
    
    Signals:
        zoom_changed: Emitted when zoom level changes.
        selection_changed: Emitted when selected annotation changes.
        image_changed: Emitted when image is loaded or cropped.
    """
    
    # Signals
    zoom_changed = Signal(float)
    selection_changed = Signal(object)  # AnnotationBase or None
    image_changed = Signal()
    crop_selection_changed = Signal(bool)  # True when crop selection exists
    color_sampled = Signal(QColor)  # Emitted when eyedropper samples a color
    text_edit_finished = Signal()  # Emitted when text editing is done (switch to Pointer)
    ocr_completed = Signal(str)  # Emitted with extracted text after OCR
    
    # Zoom limits
    MIN_ZOOM = 0.1
    MAX_ZOOM = 5.0
    
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)
        
        # Image
        self._image: Optional[QImage] = None
        
        # Annotations
        self._annotations: List[AnnotationBase] = []
        self._temp_annotations: List[AnnotationBase] = []  # Temp annotations while drawing
        self._selected_annotation: Optional[AnnotationBase] = None
        
        # State for edit tracking (undo)
        self._edit_start_state: Optional[AnnotationBase] = None
        
        # View transform
        self._zoom: float = 1.0
        self._pan_offset: QPointF = QPointF(0, 0)
        
        # Interaction state
        self._panning: bool = False
        self._pan_start: Optional[QPointF] = None
        self._space_pressed: bool = False
        
        # Tool
        self._active_tool: ToolBase = create_tool(ToolType.POINTER)
        
        # Undo/Redo
        self._undo_stack = QUndoStack(self)
        
        # Text editing (direct canvas - no widget)
        self._editing_text_annotation: Optional[TextAnnotation] = None
        self._cursor_visible: bool = True
        self._cursor_timer: Optional[QTimer] = None
        
        self._setup_widget()
        
        # Track if we're in "fit" zoom mode (auto-recalculate on resize)
        self._fit_mode: bool = True
    
    def _setup_widget(self) -> None:
        """Configure widget properties."""
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 200)
        
        # Dark background
        self.setStyleSheet("background-color: #1a1a1a;")
    
    # ─── Image Management ─────────────────────────────────────────────────
    
    def set_image(self, image: QImage) -> None:
        """
        Load a new image into the canvas.
        
        Clears annotations and resets zoom to fit.
        """
        self._image = image
        self._annotations.clear()
        self._temp_annotation = None
        self._selected_annotation = None
        self._undo_stack.clear()
        
        self.zoom_to_fit()
        self.image_changed.emit()
        self.update()
        
        self._logger.info(f"Image loaded: {image.width()}x{image.height()}")
    
    @property
    def image(self) -> Optional[QImage]:
        return self._image
    
    @property
    def image_size(self) -> tuple:
        """Return (width, height) of the image."""
        if self._image:
            return (self._image.width(), self._image.height())
        return (0, 0)
    
    @property
    def annotations(self) -> List[AnnotationBase]:
        """Return the list of annotations for inspection (e.g., by StepTool)."""
        return self._annotations
    
    # ─── Annotation Management ────────────────────────────────────────────
    
    def add_annotation(self, annotation: AnnotationBase) -> None:
        """Add an annotation with undo support."""
        cmd = AddAnnotationCommand(self, annotation)
        self._undo_stack.push(cmd)
    
    def delete_annotation(self, annotation: AnnotationBase) -> None:
        """Delete an annotation with undo support."""
        cmd = DeleteAnnotationCommand(self, annotation)
        self._undo_stack.push(cmd)
    
    def set_temp_annotation(self, annotation: Optional[AnnotationBase]) -> None:
        """Set a single temporary annotation (legacy, for Phase 2 tools)."""
        self._temp_annotations = [annotation] if annotation else []
        self.update()
    
    def add_temp_annotation(self, annotation: AnnotationBase) -> None:
        """Add a temporary annotation (while drawing)."""
        self._temp_annotations.append(annotation)
        self.update()
    
    def remove_temp_annotation(self, annotation: AnnotationBase) -> None:
        """Remove a temporary annotation."""
        if annotation in self._temp_annotations:
            self._temp_annotations.remove(annotation)
        self.update()
    
    def clear_temp_annotations(self) -> None:
        """Clear all temporary annotations."""
        self._temp_annotations.clear()
        self.update()
    
    def sample_color_at(self, pos: QPointF) -> Optional[QColor]:
        """Sample the color from the base image at the given position."""
        if not self._image:
            return None
        
        x = int(pos.x())
        y = int(pos.y())
        
        if x < 0 or x >= self._image.width() or y < 0 or y >= self._image.height():
            return None
        
        return QColor(self._image.pixel(x, y))
    
    def apply_inpaint(self, inpaint_annotation) -> None:
        """
        Apply content-aware inpainting to the base image.
        
        Uses OpenCV's inpainting algorithms to "heal" the selected region
        by analyzing boundary pixels and reconstructing the texture.
        
        Args:
            inpaint_annotation: InpaintAnnotation with the region to inpaint.
        """
        if not self._image:
            return
        
        # Store old image for undo
        old_image = self._image.copy()
        
        # Perform inpainting
        new_image = inpaint_annotation.perform_inpaint(self._image)
        
        # Apply the inpainted image
        self._image = new_image
        
        # Push undo command
        cmd = InpaintCommand(self, old_image, new_image)
        self._undo_stack.push(cmd)
        
        self.image_changed.emit()
        self.update()
        self._logger.info(f"Applied inpainting to region: {inpaint_annotation.bounding_rect}")
    
    def fill_region_with_color(self, rect: QRectF, color: QColor) -> None:
        """
        Fill a rectangular region with a solid color (used by Eraser tool).
        
        Args:
            rect: The region to fill
            color: The fill color (default white for eraser)
        """
        if not self._image:
            return
        
        # Store old image for undo
        old_image = self._image.copy()
        
        # Fill the region with the specified color
        painter = QPainter(self._image)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRect(rect.toRect())
        painter.end()
        
        # Push undo command
        cmd = InpaintCommand(self, old_image, self._image.copy())
        self._undo_stack.push(cmd)
        
        self.image_changed.emit()
        self.update()
        self._logger.info(f"Filled region with color: {rect.toRect()}")
    
    def select_annotation(self, annotation: Optional[AnnotationBase]) -> None:
        """Select an annotation."""
        if self._selected_annotation:
            self._selected_annotation.selected = False
        
        self._selected_annotation = annotation
        
        if annotation:
            annotation.selected = True
        
        self.selection_changed.emit(annotation)
        self.update()
    
    @property
    def selected_annotation(self) -> Optional[AnnotationBase]:
        return self._selected_annotation
    
    def hit_test_annotations(self, pos: QPointF) -> Optional[AnnotationBase]:
        """Find annotation at given position (image coordinates)."""
        # Test in reverse order (top-most first)
        for annotation in reversed(self._annotations):
            if annotation.hit_test(pos):
                return annotation
        return None
    
    def begin_annotation_edit(self, annotation: AnnotationBase) -> None:
        """Called when starting to edit (move/resize) an annotation."""
        self._edit_start_state = annotation.clone()
    
    def end_annotation_edit(self, annotation: AnnotationBase) -> None:
        """Called when finished editing an annotation."""
        if self._edit_start_state:
            cmd = MoveAnnotationCommand(
                self, annotation,
                self._edit_start_state,
                annotation.clone()
            )
            self._undo_stack.push(cmd)
            self._edit_start_state = None
    
    # ─── Zoom and Pan ─────────────────────────────────────────────────────
    
    @property
    def zoom(self) -> float:
        return self._zoom
    
    def set_zoom(self, zoom: float, anchor: Optional[QPointF] = None) -> None:
        """
        Set zoom level with optional anchor point.
        
        Uses anchor-point algorithm: the pixel under the anchor stays
        stationary during zoom (Shottr-like behavior).
        
        Args:
            zoom: New zoom level (clamped to MIN/MAX).
            anchor: Point to keep stationary (in widget coordinates).
                    Typically the mouse cursor position.
        """
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        
        # Exit fit mode when manually zooming
        self._fit_mode = False
        
        if anchor:
            # Anchor-point algorithm:
            # 1. Get image coordinates of the anchor point at current zoom
            img_pt = self.widget_to_image(anchor)
            
            # 2. Apply new zoom
            self._zoom = new_zoom
            
            # 3. Calculate where that image point would be at new zoom
            # new_widget_pos = img_pt * new_zoom + pan_offset
            # We want: new_widget_pos == anchor (keep it stationary)
            # So: pan_offset = anchor - img_pt * new_zoom
            self._pan_offset = QPointF(
                anchor.x() - img_pt.x() * new_zoom,
                anchor.y() - img_pt.y() * new_zoom
            )
        else:
            # No anchor - set zoom and center the image
            # This is the simple case: just set zoom and recenter
            self._zoom = new_zoom
            self._center_image()
        
        self.zoom_changed.emit(self._zoom)
        self.update()
    
    def zoom_in(self) -> None:
        """Zoom in by 25% from center."""
        center = QPointF(self.width() / 2, self.height() / 2)
        self.set_zoom(self._zoom * 1.25, center)
    
    def zoom_out(self) -> None:
        """Zoom out by 25% from center."""
        center = QPointF(self.width() / 2, self.height() / 2)
        self.set_zoom(self._zoom / 1.25, center)
    
    def zoom_to_100(self) -> None:
        """Set zoom to 100% and center image."""
        self._fit_mode = False
        self._zoom = 1.0
        self._center_image()
        self.zoom_changed.emit(self._zoom)
        self.update()
    
    def zoom_to_fit(self) -> None:
        """
        Zoom to fit image in widget.
        
        Enters 'fit mode' which auto-recalculates on window resize.
        """
        self._fit_mode = True
        self._recalculate_fit_zoom()
    
    def _recalculate_fit_zoom(self) -> None:
        """Recalculate zoom to fit image in current widget size."""
        if not self._image:
            self._zoom = 1.0
            return
        
        img_w = self._image.width()
        img_h = self._image.height()
        widget_w = self.width()
        widget_h = self.height()
        
        if img_w == 0 or img_h == 0 or widget_w == 0 or widget_h == 0:
            return
        
        # Calculate zoom to fit with some padding
        padding = 40
        zoom_x = (widget_w - padding) / img_w
        zoom_y = (widget_h - padding) / img_h
        
        # Use minimum ratio to ensure entire image is visible
        self._zoom = min(zoom_x, zoom_y)
        
        # Don't zoom beyond 100% for fit mode
        self._zoom = min(self._zoom, 1.0)
        
        self._center_image()
        self.zoom_changed.emit(self._zoom)
        self.update()
    
    def _center_image(self) -> None:
        """Center the image in the widget."""
        if not self._image:
            return
        
        img_w = self._image.width() * self._zoom
        img_h = self._image.height() * self._zoom
        
        self._pan_offset = QPointF(
            (self.width() - img_w) / 2,
            (self.height() - img_h) / 2
        )
    
    # ─── Coordinate Conversion ────────────────────────────────────────────
    
    def widget_to_image(self, pos: QPointF) -> QPointF:
        """Convert widget coordinates to image coordinates."""
        return QPointF(
            (pos.x() - self._pan_offset.x()) / self._zoom,
            (pos.y() - self._pan_offset.y()) / self._zoom
        )
    
    def image_to_widget(self, pos: QPointF) -> QPointF:
        """Convert image coordinates to widget coordinates."""
        return QPointF(
            pos.x() * self._zoom + self._pan_offset.x(),
            pos.y() * self._zoom + self._pan_offset.y()
        )
    
    # ─── Tool Management ──────────────────────────────────────────────────
    
    def set_tool(self, tool: ToolBase) -> None:
        """Set the active tool."""
        if self._active_tool:
            self._active_tool.on_deactivate(self)
        
        self._active_tool = tool
        self.setCursor(tool.cursor)
        self.update()
    
    @property
    def active_tool(self) -> ToolBase:
        return self._active_tool
    
    # ─── Crop ─────────────────────────────────────────────────────────────
    
    def crop_to_rect(self, rect: QRectF) -> None:
        """
        Crop the image to the given rectangle.
        
        Clears all annotations.
        TODO: In future phases, adjust annotations to new image coordinates.
        """
        if not self._image:
            return
        
        # Clamp rect to image bounds
        img_rect = QRectF(0, 0, self._image.width(), self._image.height())
        crop_rect = rect.intersected(img_rect)
        
        if crop_rect.width() < 1 or crop_rect.height() < 1:
            return
        
        # Store old state for undo
        old_image = self._image.copy()
        old_annotations = [a.clone() for a in self._annotations]
        
        # Crop the image
        new_image = self._image.copy(crop_rect.toRect())
        
        # Push undo command
        cmd = CropCommand(self, old_image, new_image, old_annotations)
        self._undo_stack.push(cmd)
    
    # ─── Undo/Redo ────────────────────────────────────────────────────────
    
    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo_stack
    
    def undo(self) -> None:
        if self._undo_stack.canUndo():
            self._undo_stack.undo()
    
    def redo(self) -> None:
        if self._undo_stack.canRedo():
            self._undo_stack.redo()
    
    # ─── OCR ───────────────────────────────────────────────────────────────
    
    def perform_ocr(self, rect: QRect) -> None:
        """
        Perform OCR on the specified region of the image.
        
        Args:
            rect: The region to extract text from (in image coordinates)
        """
        if not self._image or rect.isEmpty():
            return
        
        from src.services.ocr_service import extract_text_from_region, is_ocr_available
        
        if not is_ocr_available():
            self._logger.warning("OCR is not available - pytesseract not installed")
            self.ocr_completed.emit("")
            return
        
        # Clamp rect to image bounds
        img_rect = self._image.rect()
        clamped_rect = rect.intersected(img_rect)
        
        if clamped_rect.width() < 5 or clamped_rect.height() < 5:
            self.ocr_completed.emit("")
            return
        
        # Extract text from region
        text = extract_text_from_region(self._image, clamped_rect)
        
        self._logger.info(f"OCR extracted {len(text)} characters from region {clamped_rect}")
        
        # Emit signal with extracted text
        self.ocr_completed.emit(text)
    
    # ─── Text Editing ─────────────────────────────────────────────────────
    
    def start_text_edit(self, annotation: TextAnnotation) -> None:
        """Start direct canvas text editing (no widget overlay)."""
        self.finish_text_edit()  # Finish any previous edit
        
        self._editing_text_annotation = annotation
        self._cursor_visible = True
        
        # Start cursor blink timer
        if not self._cursor_timer:
            self._cursor_timer = QTimer(self)
            self._cursor_timer.timeout.connect(self._toggle_cursor)
        self._cursor_timer.start(500)  # 500ms blink
        
        # Ensure canvas has focus for keyboard input
        self.setFocus()
        self.update()
    
    def _toggle_cursor(self) -> None:
        """Toggle cursor visibility for blinking effect."""
        self._cursor_visible = not self._cursor_visible
        self.update()
    
    def finish_text_edit(self) -> None:
        """Finish direct canvas text editing."""
        was_editing = self._editing_text_annotation is not None
        
        if self._editing_text_annotation:
            # Delete empty text annotation
            if not self._editing_text_annotation.text:
                if self._editing_text_annotation in self._annotations:
                    self._annotations.remove(self._editing_text_annotation)
        
        # Clear editing state FIRST (before stopping timer)
        self._editing_text_annotation = None
        self._cursor_visible = False
        
        # Stop and disconnect cursor timer
        if self._cursor_timer:
            self._cursor_timer.stop()
        
        self.update()
        
        # Signal to switch back to Pointer tool (only if we were editing)
        if was_editing:
            self._logger.info("Text edit finished - emitting signal to switch to Pointer")
            self.text_edit_finished.emit()
    
    def _handle_text_input(self, event: QKeyEvent) -> bool:
        """Handle keyboard input for text editing. Returns True if handled."""
        if not self._editing_text_annotation:
            return False
        
        key = event.key()
        
        # Finish editing
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            self.finish_text_edit()
            return True
        
        # Backspace
        if key == Qt.Key.Key_Backspace:
            current = self._editing_text_annotation.text
            if current:
                self._editing_text_annotation.text = current[:-1]
            self.update()
            return True
        
        # Delete
        if key == Qt.Key.Key_Delete:
            # For now, same as backspace
            current = self._editing_text_annotation.text
            if current:
                self._editing_text_annotation.text = current[:-1]
            self.update()
            return True
        
        # Regular text input
        text = event.text()
        if text and text.isprintable():
            self._editing_text_annotation.text += text
            self.update()
            return True
        
        return False
    
    # ─── Rendering ────────────────────────────────────────────────────────
    
    def render_to_image(self) -> QImage:
        """
        Render the canvas (image + annotations) to a QImage.
        
        Used for saving/exporting and copying to clipboard.
        Includes all effects: blur regions, spotlights, and annotations.
        """
        if not self._image:
            return QImage()
        
        # Create output image
        result = self._image.copy()
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        # Step 1: Draw blur regions FIRST (over base image)
        for annotation in self._annotations:
            if annotation.annotation_type == AnnotationType.BLUR_REGION:
                blurred = annotation.create_pixelated_region(result)
                if not blurred.isNull():
                    rect = annotation.bounding_rect.toRect()
                    painter.drawImage(rect.topLeft(), blurred)
        
        # Step 2: Draw spotlight overlays (darkens outside regions)
        img_bounds = QRectF(0, 0, result.width(), result.height())
        for annotation in self._annotations:
            if annotation.annotation_type == AnnotationType.SPOTLIGHT:
                annotation.paint_overlay(painter, img_bounds)
        
        # Step 3: Draw all other annotations (shapes, text, arrows, etc.)
        for annotation in self._annotations:
            if annotation.annotation_type not in (AnnotationType.BLUR_REGION, AnnotationType.SPOTLIGHT):
                annotation.paint(painter)
            elif annotation.annotation_type == AnnotationType.SPOTLIGHT:
                annotation.paint(painter)  # Draw spotlight border
        
        painter.end()
        return result
    
    # ─── Event Handlers ───────────────────────────────────────────────────
    
    def paintEvent(self, event) -> None:
        """Paint the canvas."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        # Fill background
        painter.fillRect(self.rect(), QColor(26, 26, 26))
        
        if not self._image:
            # Draw placeholder
            painter.setPen(QColor(100, 100, 100))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No image loaded")
            return
        
        # Apply transform
        painter.translate(self._pan_offset)
        painter.scale(self._zoom, self._zoom)
        
        # Draw image
        painter.drawImage(0, 0, self._image)
        
        # Draw blur regions (must be drawn over base image before other annotations)
        self._draw_blur_regions(painter)
        
        # Draw regular annotations (except spotlight - handled separately)
        for annotation in self._annotations:
            if annotation.annotation_type != AnnotationType.SPOTLIGHT:
                annotation.paint(painter)
        
        # Draw spotlight overlays (darkens outside)
        self._draw_spotlight_overlays(painter)
        
        # Draw temporary annotations
        for temp_ann in self._temp_annotations:
            temp_ann.paint(painter)
        
        # Draw selection handles
        if self._selected_annotation:
            self._draw_selection_handles(painter, self._selected_annotation)
        
        # Draw text editing cursor
        if self._editing_text_annotation and self._cursor_visible:
            self._draw_text_cursor(painter, self._editing_text_annotation)
        
        # Draw crop rect if in crop mode
        if isinstance(self._active_tool, PointerTool):
            crop_rect = self._active_tool.crop_rect
            if crop_rect:
                self._draw_crop_overlay(painter, crop_rect)
        
        # Draw eraser tool preview if active
        if hasattr(self._active_tool, 'paint_preview'):
            self._active_tool.paint_preview(painter)
        
        painter.end()
    
    def _draw_blur_regions(self, painter: QPainter) -> None:
        """Draw pixelated/blurred regions over the base image."""
        for annotation in self._annotations:
            if annotation.annotation_type == AnnotationType.BLUR_REGION:
                # Create pixelated version and draw it
                pixelated = annotation.create_pixelated_region(self._image)
                if not pixelated.isNull():
                    rect = annotation.bounding_rect.toRect()
                    painter.drawImage(rect.topLeft(), pixelated)
        
        # Also check temp annotations
        for annotation in self._temp_annotations:
            if annotation.annotation_type == AnnotationType.BLUR_REGION:
                pixelated = annotation.create_pixelated_region(self._image)
                if not pixelated.isNull():
                    rect = annotation.bounding_rect.toRect()
                    painter.drawImage(rect.topLeft(), pixelated)
    
    def _draw_spotlight_overlays(self, painter: QPainter) -> None:
        """Draw spotlight annotations with darkened outside regions."""
        if not self._image:
            return
        
        img_bounds = QRectF(0, 0, self._image.width(), self._image.height())
        
        for annotation in self._annotations:
            if annotation.annotation_type == AnnotationType.SPOTLIGHT:
                annotation.paint_overlay(painter, img_bounds)
                annotation.paint(painter)  # Draw the border
        
        for annotation in self._temp_annotations:
            if annotation.annotation_type == AnnotationType.SPOTLIGHT:
                annotation.paint_overlay(painter, img_bounds)
                annotation.paint(painter)
    
    def _draw_selection_handles(
        self, painter: QPainter, annotation: AnnotationBase
    ) -> None:
        """Draw selection handles around an annotation."""
        handles = annotation.get_resize_handles()
        
        # Draw bounding rect
        painter.setPen(QPen(QColor(80, 144, 208), 1 / self._zoom))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(annotation.bounding_rect)
        
        # Draw handles
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(80, 144, 208), 1 / self._zoom))
        
        handle_size = 8 / self._zoom
        for handle in handles:
            # Adjust handle size for zoom
            center = handle.center()
            adjusted = QRectF(
                center.x() - handle_size / 2,
                center.y() - handle_size / 2,
                handle_size,
                handle_size
            )
            painter.drawRect(adjusted)
        
        # Draw spike handle for TextAnnotation
        from src.editor.annotations import TextAnnotation
        if isinstance(annotation, TextAnnotation) and annotation.spike_enabled:
            spike_tip = annotation.spike_tip
            spike_handle_size = 10 / self._zoom
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(80, 144, 208), 2 / self._zoom))
            painter.drawEllipse(
                spike_tip,
                spike_handle_size / 2,
                spike_handle_size / 2
            )
    
    def _draw_text_cursor(
        self, painter: QPainter, annotation: TextAnnotation
    ) -> None:
        """Draw blinking cursor inside the text annotation bubble."""
        from PySide6.QtGui import QFont, QFontMetrics
        
        # Get font info
        font = QFont()
        font.setPixelSize(annotation.style.font_size)
        metrics = QFontMetrics(font)
        
        # Calculate cursor position (at end of text)
        text = annotation.text or ""
        text_width = metrics.horizontalAdvance(text)
        
        # Cursor position in image coordinates
        cursor_x = annotation.position.x() + text_width
        cursor_y = annotation.position.y() - metrics.ascent()
        cursor_height = metrics.height()
        
        # Draw cursor line (white for visibility on red bubble)
        painter.setPen(QPen(QColor(255, 255, 255), 2 / self._zoom))
        painter.drawLine(
            QPointF(cursor_x, cursor_y),
            QPointF(cursor_x, cursor_y + cursor_height)
        )
    
    def _draw_crop_overlay(self, painter: QPainter, crop_rect: QRectF) -> None:
        """Draw crop overlay with dimmed regions outside crop area."""
        if not self._image:
            return
        
        img_rect = QRectF(0, 0, self._image.width(), self._image.height())
        
        # Dim outside crop area
        painter.setBrush(QColor(0, 0, 0, 128))
        painter.setPen(Qt.PenStyle.NoPen)
        
        # Top region
        painter.drawRect(QRectF(0, 0, img_rect.width(), crop_rect.top()))
        # Bottom region
        painter.drawRect(QRectF(0, crop_rect.bottom(), img_rect.width(), img_rect.height() - crop_rect.bottom()))
        # Left region
        painter.drawRect(QRectF(0, crop_rect.top(), crop_rect.left(), crop_rect.height()))
        # Right region
        painter.drawRect(QRectF(crop_rect.right(), crop_rect.top(), img_rect.right() - crop_rect.right(), crop_rect.height()))
        
        # Draw crop border
        painter.setPen(QPen(QColor(255, 255, 255), 2 / self._zoom))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(crop_rect)
    
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._space_pressed:
                # Start panning
                self._panning = True
                self._pan_start = event.position()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                # Delegate to tool
                img_pos = self.widget_to_image(event.position())
                self._active_tool.on_mouse_press(img_pos, self, event.modifiers())
    
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move."""
        if self._panning and self._pan_start:
            delta = event.position() - self._pan_start
            self._pan_offset += QPointF(delta.x(), delta.y())
            self._pan_start = event.position()
            self.update()
        else:
            img_pos = self.widget_to_image(event.position())
            self._active_tool.on_mouse_move(img_pos, self, event.modifiers())
            
            # Update cursor based on what's under the mouse
            if not self._space_pressed:
                self._update_cursor_for_position(img_pos)
    
    def _update_cursor_for_position(self, img_pos: QPointF) -> None:
        """Update cursor based on what's under the mouse position."""
        # Check if over a resize handle on selected annotation
        selected = self._selected_annotation
        if selected:
            handle_index = selected.hit_test_handle(img_pos)
            if handle_index >= 0:
                cursor = self._get_resize_cursor(handle_index, selected)
                self.setCursor(cursor)
                return
        
        # Check if over any annotation body
        hit = self.hit_test_annotations(img_pos)
        if hit:
            self.setCursor(Qt.CursorShape.SizeAllCursor)  # Move cursor
            return
        
        # Default to tool cursor (crosshair for drawing tools)
        self.setCursor(self._active_tool.cursor)
    
    def _get_resize_cursor(self, handle_index: int, annotation: AnnotationBase) -> Qt.CursorShape:
        """Get the appropriate resize cursor for a handle index."""
        # Handle order: TL(0), TC(1), TR(2), ML(3), MR(4), BL(5), BC(6), BR(7)
        # For arrows with only 2 handles: 0=start, 7=end
        
        from src.editor.annotations import ArrowAnnotation
        
        if isinstance(annotation, ArrowAnnotation):
            # Arrows have start/end handles - use cross cursor
            return Qt.CursorShape.CrossCursor
        
        # Standard 8-handle resize cursors
        resize_cursors = {
            0: Qt.CursorShape.SizeFDiagCursor,  # TL - diagonal /
            1: Qt.CursorShape.SizeVerCursor,    # TC - vertical
            2: Qt.CursorShape.SizeBDiagCursor,  # TR - diagonal \
            3: Qt.CursorShape.SizeHorCursor,    # ML - horizontal
            4: Qt.CursorShape.SizeHorCursor,    # MR - horizontal
            5: Qt.CursorShape.SizeBDiagCursor,  # BL - diagonal \
            6: Qt.CursorShape.SizeVerCursor,    # BC - vertical
            7: Qt.CursorShape.SizeFDiagCursor,  # BR - diagonal /
        }
        
        return resize_cursors.get(handle_index, Qt.CursorShape.ArrowCursor)
    
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._panning:
                self._panning = False
                self.setCursor(
                    Qt.CursorShape.OpenHandCursor if self._space_pressed
                    else self._active_tool.cursor
                )
            else:
                img_pos = self.widget_to_image(event.position())
                self._active_tool.on_mouse_release(img_pos, self, event.modifiers())
    
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handle mouse wheel for zooming."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else 0.9
            self.set_zoom(self._zoom * factor, event.position())
            event.accept()
        else:
            event.ignore()
    
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle key press."""
        # Handle text editing input first
        if self._handle_text_input(event):
            return
        
        key = event.key()
        modifiers = event.modifiers()
        
        # Space for panning
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        
        # Zoom shortcuts
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Plus or key == Qt.Key.Key_Equal:
                self.zoom_in()
                return
            elif key == Qt.Key.Key_Minus:
                self.zoom_out()
                return
            elif key == Qt.Key.Key_0:
                self.zoom_to_100()
                return
            elif key == Qt.Key.Key_Z:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self.redo()
                else:
                    self.undo()
                return
            elif key == Qt.Key.Key_Y:
                self.redo()
                return
        
        # Delegate to tool
        if self._active_tool.on_key_press(key, self, modifiers):
            return
        
        # Delete key to delete selected annotation (not during text editing)
        if key == Qt.Key.Key_Delete and self._selected_annotation:
            self.delete_annotation(self._selected_annotation)
            return
        
        super().keyPressEvent(event)
    
    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """Handle key release."""
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            self.setCursor(self._active_tool.cursor)
    
    def resizeEvent(self, event) -> None:
        """
        Handle widget resize.
        
        If in fit mode, recalculate zoom to keep image fully visible.
        Otherwise, keep the center point stationary.
        """
        super().resizeEvent(event)
        
        if not self._image:
            return
        
        if self._fit_mode:
            # Recalculate fit zoom when window resizes (Shottr behavior)
            self._recalculate_fit_zoom()
        else:
            # Keep the center of the view stable during resize
            # This prevents jarring jumps when resizing the window
            self._center_image()
