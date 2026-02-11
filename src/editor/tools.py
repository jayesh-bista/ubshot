"""
Tool framework and implementations for UbShot editor.

This module provides the tool system for the editor canvas. Each tool
handles mouse and keyboard events and manipulates annotations.

Tools:
- PointerTool: Select, move, resize annotations; crop mode
- RectangleTool: Draw rectangle annotations
- EllipseTool: Draw ellipse annotations
- ArrowTool: Draw arrow annotations
- TextTool: Add/edit text annotations
- FreehandTool: Freehand drawing (Phase 3)
- HighlighterTool: Semi-transparent marker (Phase 3)
- SpotlightTool: Spotlight effect (Phase 3)
- BlurTool: Blur/pixelate regions (Phase 3)
- StepTool: Numbered step badges (Phase 3)
- EraserTool: Delete annotations (Phase 3)
- EyedropperTool: Pick colors (Phase 3)
- RulerTool: Measure distances (Phase 3)

TODO (Future Phases):
- MagnifierTool
- BendableArrowTool
"""

from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QClipboard, QPainter, QPen

from src.editor.annotations import (
    AnnotationBase,
    AnnotationStyle,
    AnnotationType,
    ArrowAnnotation,
    BlurRegionAnnotation,
    EllipseAnnotation,
    FreehandAnnotation,
    HighlightAnnotation,
    InpaintAnnotation,
    RectangleAnnotation,
    SpotlightAnnotation,
    StepAnnotation,
    TextAnnotation,
)
from src.services.logging_service import get_logger

if TYPE_CHECKING:
    from src.editor.editor_canvas import EditorCanvas


class ToolType(Enum):
    """Enum for tool types."""
    POINTER = auto()
    RECTANGLE = auto()
    ELLIPSE = auto()
    ARROW = auto()
    TEXT = auto()
    # Phase 3 tools
    FREEHAND = auto()
    HIGHLIGHTER = auto()
    SPOTLIGHT = auto()
    BLUR = auto()
    STEP = auto()
    ERASER = auto()
    OCR = auto()


class ToolBase(ABC):
    """
    Base class for all tools.
    
    Tools handle mouse and keyboard events from the canvas and
    manipulate annotations accordingly.
    """
    
    def __init__(self) -> None:
        self._logger = get_logger(__name__)
        self._style: AnnotationStyle = AnnotationStyle()
    
    @property
    @abstractmethod
    def tool_type(self) -> ToolType:
        """Return the type of this tool."""
        pass
    
    @property
    @abstractmethod
    def cursor(self) -> Qt.CursorShape:
        """Return the cursor to use when this tool is active."""
        pass
    
    @property
    def style(self) -> AnnotationStyle:
        """Get the current style for new annotations."""
        return self._style
    
    @style.setter
    def style(self, value: AnnotationStyle) -> None:
        """Set the style for new annotations."""
        self._style = value
    
    @abstractmethod
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        """Handle mouse press event."""
        pass
    
    @abstractmethod
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        """Handle mouse move event."""
        pass
    
    @abstractmethod
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        """Handle mouse release event."""
        pass
    
    def on_key_press(
        self,
        key: int,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> bool:
        """
        Handle key press event.
        
        Returns True if the event was handled.
        """
        return False
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        """Called when tool is deactivated (another tool selected)."""
        pass


class PointerTool(ToolBase):
    """
    Pointer/Select tool for selecting, moving, and resizing annotations.
    
    Shottr-like behavior:
    - Click on annotation: Select it
    - Drag annotation: Move it
    - Drag handle: Resize it
    - Drag on empty canvas: Create crop selection
    - Enter: Apply crop
    - Escape: Cancel crop
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._dragging = False
        self._resizing = False
        self._resize_handle: int = -1
        self._drag_start: Optional[QPointF] = None
        self._drag_annotation: Optional[AnnotationBase] = None
        
        # Crop state (no separate mode - it's automatic)
        self._crop_rect: Optional[QRectF] = None
        self._crop_start: Optional[QPointF] = None
        self._is_cropping: bool = False
        
        # Spike dragging for TextAnnotation
        self._dragging_spike: bool = False
        self._spike_annotation: Optional[TextAnnotation] = None
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.POINTER
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.ArrowCursor
    
    @property
    def crop_rect(self) -> Optional[QRectF]:
        return self._crop_rect
    
    @property
    def has_crop_selection(self) -> bool:
        """Check if there's a valid crop selection."""
        return (self._crop_rect is not None and 
                self._crop_rect.width() > 10 and 
                self._crop_rect.height() > 10)
    
    def clear_crop(self) -> None:
        """Clear the crop selection."""
        self._crop_rect = None
        self._crop_start = None
        self._is_cropping = False
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        self._drag_start = pos
        
        # Check if clicking on spike handle of a TextAnnotation
        selected = canvas.selected_annotation
        if selected and isinstance(selected, TextAnnotation):
            if selected.hit_test_spike_handle(pos):
                self._dragging_spike = True
                self._spike_annotation = selected
                return
        
        # Check if clicking on a resize handle of selected annotation
        if selected:
            handle = selected.hit_test_handle(pos)
            if handle >= 0:
                self._resizing = True
                self._resize_handle = handle
                self._drag_annotation = selected
                canvas.begin_annotation_edit(selected)
                # Clear any crop selection when manipulating annotations
                self.clear_crop()
                canvas.update()
                return
        
        # Check if clicking on any annotation
        hit_annotation = canvas.hit_test_annotations(pos)
        
        if hit_annotation:
            canvas.select_annotation(hit_annotation)
            self._dragging = True
            self._drag_annotation = hit_annotation
            canvas.begin_annotation_edit(hit_annotation)
            # Clear any crop selection when selecting annotations
            self.clear_crop()
            canvas.update()
        else:
            # Empty canvas click - start crop selection (Shottr-like)
            canvas.select_annotation(None)
            self._dragging = False
            self._drag_annotation = None
            
            # Start crop selection
            self._is_cropping = True
            self._crop_start = pos
            self._crop_rect = QRectF(pos, pos)
            canvas.update()
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Handle spike dragging for TextAnnotation
        if self._dragging_spike and self._spike_annotation:
            self._spike_annotation.move_spike(pos)
            canvas.update()
            return
        
        # Handle crop selection drag
        if self._is_cropping and self._crop_start:
            self._crop_rect = QRectF(self._crop_start, pos).normalized()
            canvas.update()
            return
        
        if self._resizing and self._drag_annotation:
            self._drag_annotation.resize(self._resize_handle, pos)
            canvas.update()
            return
        
        if self._dragging and self._drag_annotation and self._drag_start:
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            self._drag_annotation.move_by(dx, dy)
            self._drag_start = pos
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._is_cropping:
            self._is_cropping = False
            # Keep crop rect if valid size, else clear it
            if not self.has_crop_selection:
                self._crop_rect = None
            canvas.update()
            # Notify canvas to show crop UI
            canvas.crop_selection_changed.emit(self.has_crop_selection)
        
        if self._dragging or self._resizing:
            if self._drag_annotation:
                canvas.end_annotation_edit(self._drag_annotation)
        
        self._dragging = False
        self._resizing = False
        self._resize_handle = -1
        self._drag_start = None
        self._drag_annotation = None
        self._dragging_spike = False
        self._spike_annotation = None
    
    def on_key_press(
        self,
        key: int,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> bool:
        # Delete selected annotation with Delete or Backspace
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selected = canvas.selected_annotation
            if selected:
                canvas.delete_annotation(selected)
                return True
        
        # Enter to apply crop
        if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            if self.has_crop_selection:
                self.apply_crop(canvas)
                return True
        
        # Escape to cancel crop
        if key == Qt.Key.Key_Escape:
            if self._crop_rect:
                self.clear_crop()
                canvas.update()
                canvas.crop_selection_changed.emit(False)
                return True
        
        return False
    
    def apply_crop(self, canvas: "EditorCanvas") -> bool:
        """
        Apply the current crop selection.
        
        Returns True if crop was applied successfully.
        """
        if not self.has_crop_selection:
            return False
        
        canvas.crop_to_rect(self._crop_rect)
        self.clear_crop()
        canvas.crop_selection_changed.emit(False)
        return True
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        self.clear_crop()
        canvas.crop_selection_changed.emit(False)


class ShapeToolMixin:
    """
    Mixin for shape tools to support selecting, moving, and resizing 
    existing annotations when not drawing.
    """
    
    def _init_mixin(self) -> None:
        """Initialize mixin state."""
        self._dragging_existing: bool = False
        self._resizing_existing: bool = False
        self._resize_handle: int = -1
        self._drag_start: Optional[QPointF] = None
        self._manipulating_annotation: Optional[AnnotationBase] = None
    
    def _handle_existing_annotation(
        self,
        pos: QPointF,
        canvas: "EditorCanvas"
    ) -> bool:
        """
        Check if clicking on existing annotation for selection/manipulation.
        Returns True if handled, False if should proceed with drawing.
        """
        # First check resize handles on selected annotation
        selected = canvas.selected_annotation
        if selected:
            handle = selected.hit_test_handle(pos)
            if handle >= 0:
                self._resizing_existing = True
                self._resize_handle = handle
                self._manipulating_annotation = selected
                self._drag_start = pos
                canvas.begin_annotation_edit(selected)
                return True
        
        # Check if clicking on any annotation
        hit = canvas.hit_test_annotations(pos)
        if hit:
            canvas.select_annotation(hit)
            self._dragging_existing = True
            self._manipulating_annotation = hit
            self._drag_start = pos
            canvas.begin_annotation_edit(hit)
            return True
        
        return False
    
    def _handle_mouse_move_existing(
        self,
        pos: QPointF,
        canvas: "EditorCanvas"
    ) -> bool:
        """Handle mouse move for existing annotation manipulation."""
        if self._resizing_existing and self._manipulating_annotation:
            self._manipulating_annotation.resize(self._resize_handle, pos)
            canvas.update()
            return True
        
        if self._dragging_existing and self._manipulating_annotation and self._drag_start:
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            self._manipulating_annotation.move_by(dx, dy)
            self._drag_start = pos
            canvas.update()
            return True
        
        return False
    
    def _handle_mouse_release_existing(
        self,
        canvas: "EditorCanvas"
    ) -> bool:
        """Handle mouse release for existing annotation manipulation."""
        if self._dragging_existing or self._resizing_existing:
            if self._manipulating_annotation:
                canvas.end_annotation_edit(self._manipulating_annotation)
            
            self._dragging_existing = False
            self._resizing_existing = False
            self._resize_handle = -1
            self._drag_start = None
            self._manipulating_annotation = None
            return True
        
        return False


class RectangleTool(ToolBase, ShapeToolMixin):
    """
    Tool for drawing rectangle annotations.
    Also supports selecting/moving/resizing existing annotations.
    """
    
    def __init__(self) -> None:
        ToolBase.__init__(self)
        self._init_mixin()
        # Rectangle default stroke width
        self._style.stroke_width = 5
        self._start_pos: Optional[QPointF] = None
        self._current_rect: Optional[RectangleAnnotation] = None
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.RECTANGLE
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Check if manipulating existing annotation
        if self._handle_existing_annotation(pos, canvas):
            self._start_pos = None
            self._current_rect = None
            return
        
        # Deselect and start new drawing
        canvas.select_annotation(None)
        self._start_pos = pos
        self._current_rect = RectangleAnnotation(
            QRectF(pos, pos),
            self._style.clone()
        )
        canvas.set_temp_annotation(self._current_rect)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Handle existing annotation manipulation
        if self._handle_mouse_move_existing(pos, canvas):
            return
        
        # Handle new drawing
        if self._current_rect and self._start_pos:
            rect = QRectF(self._start_pos, pos).normalized()
            
            # Hold Shift for square
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                size = max(rect.width(), rect.height())
                rect.setWidth(size)
                rect.setHeight(size)
            
            self._current_rect.bounding_rect = rect
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Handle existing annotation manipulation
        if self._handle_mouse_release_existing(canvas):
            return
        
        # Handle new drawing completion
        if self._current_rect and self._start_pos:
            rect = QRectF(self._start_pos, pos).normalized()
            
            # Only add if rectangle is large enough
            if rect.width() > 3 and rect.height() > 3:
                self._current_rect.bounding_rect = rect
                canvas.add_annotation(self._current_rect)
                canvas.select_annotation(self._current_rect)
        
        canvas.set_temp_annotation(None)
        self._start_pos = None
        self._current_rect = None
    
    def on_key_press(
        self,
        key: int,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> bool:
        # Delete selected annotation
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selected = canvas.selected_annotation
            if selected:
                canvas.delete_annotation(selected)
                return True
        return False


class EllipseTool(ToolBase, ShapeToolMixin):
    """
    Tool for drawing ellipse annotations.
    Also supports selecting/moving/resizing existing annotations.
    """
    
    def __init__(self) -> None:
        ToolBase.__init__(self)
        self._init_mixin()
        # Ellipse default stroke width
        self._style.stroke_width = 5
        self._start_pos: Optional[QPointF] = None
        self._current_ellipse: Optional[EllipseAnnotation] = None
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.ELLIPSE
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Check if manipulating existing annotation
        if self._handle_existing_annotation(pos, canvas):
            self._start_pos = None
            self._current_ellipse = None
            return
        
        # Deselect and start new drawing
        canvas.select_annotation(None)
        self._start_pos = pos
        self._current_ellipse = EllipseAnnotation(
            QRectF(pos, pos),
            self._style.clone()
        )
        canvas.set_temp_annotation(self._current_ellipse)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Handle existing annotation manipulation
        if self._handle_mouse_move_existing(pos, canvas):
            return
        
        # Handle new drawing
        if self._current_ellipse and self._start_pos:
            rect = QRectF(self._start_pos, pos).normalized()
            
            # Hold Shift for circle
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                size = max(rect.width(), rect.height())
                rect.setWidth(size)
                rect.setHeight(size)
            
            self._current_ellipse.bounding_rect = rect
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Handle existing annotation manipulation
        if self._handle_mouse_release_existing(canvas):
            return
        
        # Handle new drawing completion
        if self._current_ellipse and self._start_pos:
            rect = QRectF(self._start_pos, pos).normalized()
            
            if rect.width() > 3 and rect.height() > 3:
                self._current_ellipse.bounding_rect = rect
                canvas.add_annotation(self._current_ellipse)
                canvas.select_annotation(self._current_ellipse)
        
        canvas.set_temp_annotation(None)
        self._start_pos = None
        self._current_ellipse = None
    
    def on_key_press(
        self,
        key: int,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> bool:
        # Delete selected annotation
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selected = canvas.selected_annotation
            if selected:
                canvas.delete_annotation(selected)
                return True
        return False


class ArrowTool(ToolBase, ShapeToolMixin):
    """
    Tool for drawing arrow annotations.
    Also supports selecting/moving/resizing existing annotations.
    """
    
    def __init__(self) -> None:
        ToolBase.__init__(self)
        self._init_mixin()
        # Arrow default stroke width
        self._style.stroke_width = 5
        self._start_pos: Optional[QPointF] = None
        self._current_arrow: Optional[ArrowAnnotation] = None
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.ARROW
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Check if manipulating existing annotation
        if self._handle_existing_annotation(pos, canvas):
            self._start_pos = None
            self._current_arrow = None
            return
        
        # Deselect and start new drawing
        canvas.select_annotation(None)
        self._start_pos = pos
        self._current_arrow = ArrowAnnotation(
            pos, pos,
            self._style.clone()
        )
        canvas.set_temp_annotation(self._current_arrow)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Handle existing annotation manipulation
        if self._handle_mouse_move_existing(pos, canvas):
            return
        
        # Handle new drawing
        if self._current_arrow:
            # Hold Shift for 45-degree angles
            if modifiers & Qt.KeyboardModifier.ShiftModifier and self._start_pos:
                pos = self._snap_to_angle(self._start_pos, pos)
            
            self._current_arrow.end = pos
            canvas.update()
    
    def _snap_to_angle(self, start: QPointF, end: QPointF) -> QPointF:
        """Snap the end point to 45-degree angle increments."""
        import math
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        
        angle = math.atan2(dy, dx)
        distance = math.sqrt(dx * dx + dy * dy)
        
        # Snap to nearest 45 degrees
        snapped_angle = round(angle / (math.pi / 4)) * (math.pi / 4)
        
        return QPointF(
            start.x() + distance * math.cos(snapped_angle),
            start.y() + distance * math.sin(snapped_angle)
        )
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Handle existing annotation manipulation
        if self._handle_mouse_release_existing(canvas):
            return
        
        # Handle new drawing completion
        if self._current_arrow and self._start_pos:
            # Only add if arrow is long enough
            dx = pos.x() - self._start_pos.x()
            dy = pos.y() - self._start_pos.y()
            length = (dx * dx + dy * dy) ** 0.5
            
            if length > 10:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    pos = self._snap_to_angle(self._start_pos, pos)
                self._current_arrow.end = pos
                canvas.add_annotation(self._current_arrow)
                canvas.select_annotation(self._current_arrow)
        
        canvas.set_temp_annotation(None)
        self._start_pos = None
        self._current_arrow = None
    
    def on_key_press(
        self,
        key: int,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> bool:
        # Delete selected annotation
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selected = canvas.selected_annotation
            if selected:
                canvas.delete_annotation(selected)
                return True
        return False


class TextTool(ToolBase):
    """
    Shottr-style text tool with pointy note bubbles.
    
    Features:
    - Click to create text bubble with red background
    - Inline text editing
    - Draggable spike/pointer to reference content
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._pending_annotation: Optional[TextAnnotation] = None
        self._dragging_spike: bool = False
        self._current_text_annotation: Optional[TextAnnotation] = None
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.TEXT
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.IBeamCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Check if clicking on spike handle of existing annotation
        for annotation in canvas.annotations:
            if isinstance(annotation, TextAnnotation):
                if annotation.hit_test_spike_handle(pos):
                    # Start dragging spike
                    self._dragging_spike = True
                    self._current_text_annotation = annotation
                    return
        
        # Check if clicking on existing text annotation
        hit = canvas.hit_test_annotations(pos)
        if hit and isinstance(hit, TextAnnotation):
            # Select and start editing
            canvas.select_annotation(hit)
            canvas.start_text_edit(hit)
            self._current_text_annotation = hit
            return
        
        # If currently editing, finish and return (don't create new annotation)
        # The signal will switch to Pointer tool
        if canvas._editing_text_annotation:
            canvas.finish_text_edit()
            return
        
        # Create new text annotation
        new_text = TextAnnotation(pos, "", self._style.clone())
        canvas.add_annotation(new_text)
        canvas.select_annotation(new_text)
        canvas.start_text_edit(new_text)
        self._pending_annotation = new_text
        self._current_text_annotation = new_text
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._dragging_spike and self._current_text_annotation:
            # Move the spike to new position
            self._current_text_annotation.move_spike(pos)
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._dragging_spike:
            self._dragging_spike = False
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        canvas.finish_text_edit()
        self._pending_annotation = None
        self._current_text_annotation = None
        self._dragging_spike = False


def create_tool(tool_type: ToolType) -> ToolBase:
    """
    Factory function to create tools by type.
    
    Args:
        tool_type: The type of tool to create.
        
    Returns:
        A new instance of the requested tool.
    """
    tool_classes = {
        ToolType.POINTER: PointerTool,
        ToolType.RECTANGLE: RectangleTool,
        ToolType.ELLIPSE: EllipseTool,
        ToolType.ARROW: ArrowTool,
        ToolType.TEXT: TextTool,
        # Phase 3 tools
        ToolType.FREEHAND: FreehandTool,
        ToolType.HIGHLIGHTER: HighlighterTool,
        ToolType.SPOTLIGHT: SpotlightTool,
        ToolType.BLUR: BlurTool,
        ToolType.STEP: StepTool,
        ToolType.ERASER: EraserTool,
        ToolType.OCR: OCRTool,
    }
    
    if tool_type not in tool_classes:
        raise ValueError(f"Unknown tool type: {tool_type}")
    
    return tool_classes[tool_type]()


# ─── Phase 3 Tools ─────────────────────────────────────────────────────────────


class FreehandTool(ToolBase):
    """
    Freehand drawing tool.
    
    Creates smooth polyline paths by tracking mouse movement.
    """
    
    def __init__(self) -> None:
        super().__init__()
        # Freehand default stroke width
        self._style.stroke_width = 5
        self._current_annotation: Optional[FreehandAnnotation] = None
        self._is_drawing: bool = False
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.FREEHAND
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Start new freehand path
        self._current_annotation = FreehandAnnotation(style=self._style.clone())
        self._current_annotation.add_point(pos)
        self._is_drawing = True
        canvas.add_temp_annotation(self._current_annotation)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._is_drawing and self._current_annotation:
            self._current_annotation.add_point(pos)
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._is_drawing and self._current_annotation:
            # Only add if path has enough points
            if len(self._current_annotation.points) > 2:
                canvas.remove_temp_annotation(self._current_annotation)
                canvas.add_annotation(self._current_annotation)
            else:
                canvas.remove_temp_annotation(self._current_annotation)
            
            self._current_annotation = None
            self._is_drawing = False
            canvas.update()
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        if self._current_annotation:
            canvas.remove_temp_annotation(self._current_annotation)
        self._current_annotation = None
        self._is_drawing = False


class HighlighterTool(ToolBase):
    """
    Highlighter tool with Multiply blend mode and axis-locking.
    
    Features:
    - Multiply blend mode keeps underlying text crisp
    - Thick stroke with rounded caps (marker effect)
    - Auto-detects near-horizontal/vertical strokes and snaps to straight line
    """
    
    def __init__(self) -> None:
        super().__init__()
        # Default highlighter style
        self._style = AnnotationStyle()
        self._style.stroke_color = QColor(255, 255, 0)  # Yellow
        self._style.stroke_width = 25  # Thicker stroke for highlighter
        self._style.opacity = 1.0  # Full opacity - blend mode handles tinting
        
        self._current_annotation: Optional[HighlightAnnotation] = None
        self._is_drawing: bool = False
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.HIGHLIGHTER
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        self._current_annotation = HighlightAnnotation(style=self._style.clone())
        self._current_annotation.add_point(pos)
        self._is_drawing = True
        canvas.add_temp_annotation(self._current_annotation)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._is_drawing and self._current_annotation:
            # Use axis-locking algorithm for straight lines
            self._current_annotation.add_point_with_axis_lock(pos)
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._is_drawing and self._current_annotation:
            if len(self._current_annotation.points) >= 2:
                canvas.remove_temp_annotation(self._current_annotation)
                canvas.add_annotation(self._current_annotation)
            else:
                canvas.remove_temp_annotation(self._current_annotation)
            
            self._current_annotation = None
            self._is_drawing = False
            canvas.update()
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        if self._current_annotation:
            canvas.remove_temp_annotation(self._current_annotation)
        self._current_annotation = None
        self._is_drawing = False


class SpotlightTool(ToolBase):
    """
    Spotlight tool - darken everything outside the selected region.
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._start_pos: Optional[QPointF] = None
        self._current_annotation: Optional[SpotlightAnnotation] = None
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.SPOTLIGHT
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        self._start_pos = pos
        self._current_annotation = SpotlightAnnotation(
            QRectF(pos, pos),
            self._style.clone(),
            is_circle=modifiers & Qt.KeyboardModifier.ShiftModifier
        )
        canvas.add_temp_annotation(self._current_annotation)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._start_pos and self._current_annotation:
            rect = QRectF(self._start_pos, pos).normalized()
            self._current_annotation.bounding_rect = rect
            self._current_annotation.is_circle = modifiers & Qt.KeyboardModifier.ShiftModifier
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._current_annotation:
            rect = self._current_annotation.bounding_rect
            if rect.width() > 10 and rect.height() > 10:
                canvas.remove_temp_annotation(self._current_annotation)
                canvas.add_annotation(self._current_annotation)
            else:
                canvas.remove_temp_annotation(self._current_annotation)
            
            self._current_annotation = None
            self._start_pos = None
            canvas.update()
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        if self._current_annotation:
            canvas.remove_temp_annotation(self._current_annotation)
        self._current_annotation = None
        self._start_pos = None


class BlurTool(ToolBase):
    """
    Blur/Pixelate tool - applies frosted glass blur or pixelation to a region.
    
    Default mode is 'blur' for smooth frosted glass effect using Gaussian blur.
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._start_pos: Optional[QPointF] = None
        self._current_annotation: Optional[BlurRegionAnnotation] = None
        self.mode: str = "blur"  # "blur" for frosted glass, "pixelate" for mosaic
        self.intensity: int = 35  # Higher = stronger blur/larger pixel blocks
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.BLUR
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        self._start_pos = pos
        self._current_annotation = BlurRegionAnnotation(
            QRectF(pos, pos),
            self._style.clone(),
            mode=self.mode
        )
        self._current_annotation.intensity = self.intensity
        canvas.add_temp_annotation(self._current_annotation)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._start_pos and self._current_annotation:
            rect = QRectF(self._start_pos, pos).normalized()
            self._current_annotation.bounding_rect = rect
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._current_annotation:
            rect = self._current_annotation.bounding_rect
            if rect.width() > 10 and rect.height() > 10:
                canvas.remove_temp_annotation(self._current_annotation)
                canvas.add_annotation(self._current_annotation)
            else:
                canvas.remove_temp_annotation(self._current_annotation)
            
            self._current_annotation = None
            self._start_pos = None
            canvas.update()
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        if self._current_annotation:
            canvas.remove_temp_annotation(self._current_annotation)
        self._current_annotation = None
        self._start_pos = None


class StepTool(ToolBase):
    """
    Step counter tool - place numbered badges for step-by-step annotations.
    
    Uses dynamic numbering: scans canvas for existing step annotations and
    assigns the next number as max + 1. This ensures deletions, undos, or
    clearing the canvas automatically adjusts the sequence.
    """
    
    def __init__(self) -> None:
        super().__init__()
        # Step counter always uses fixed red color
        self._style.stroke_color = QColor(211, 78, 78)  # #D34E4E
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.STEP
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def _get_next_step_number(self, canvas: "EditorCanvas") -> int:
        """
        Determine the next step number by scanning existing step annotations.
        
        Returns max existing step number + 1, or 1 if no steps exist.
        """
        max_number = 0
        for annotation in canvas.annotations:
            if annotation.annotation_type == AnnotationType.STEP:
                if hasattr(annotation, 'number'):
                    max_number = max(max_number, annotation.number)
        return max_number + 1
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        # Get next step number dynamically
        next_number = self._get_next_step_number(canvas)
        
        # Create step annotation
        annotation = StepAnnotation(pos, next_number, self._style.clone())
        annotation.circle_color = self._style.stroke_color
        canvas.add_annotation(annotation)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        pass
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        pass
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        pass


class EraserTool(ToolBase):
    """
    Eraser tool - fills the selected area with white.
    
    Drag to select a rectangular region, release to fill with white.
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._start_pos: Optional[QPointF] = None
        self._current_rect: Optional[QRectF] = None
        self.fill_color: QColor = QColor(255, 255, 255)  # White
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.ERASER
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        self._start_pos = pos
        self._current_rect = QRectF(pos, pos)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._start_pos:
            self._current_rect = QRectF(self._start_pos, pos).normalized()
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._current_rect:
            rect = self._current_rect
            if rect.width() > 5 and rect.height() > 5:
                # Fill the region with white
                canvas.fill_region_with_color(rect, self.fill_color)
            
            self._current_rect = None
            self._start_pos = None
            canvas.update()
    
    def paint_preview(self, painter: QPainter) -> None:
        """Paint the selection preview rectangle."""
        if self._current_rect:
            pen = QPen(QColor(255, 100, 100, 200))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(255, 255, 255, 100))
            painter.drawRect(self._current_rect)
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        self._current_rect = None
        self._start_pos = None


class OCRTool(ToolBase):
    """
    OCR Tool - Select a region and extract text using optical character recognition.
    
    Usage:
    - Click and drag to select region containing text
    - On release, text is extracted and copied to clipboard
    - A popup shows the extracted text
    - Tool switches to Pointer after extraction
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._start_pos: Optional[QPointF] = None
        self._current_rect: Optional[QRectF] = None
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.OCR
    
    @property
    def cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.CrossCursor
    
    def on_mouse_press(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        self._start_pos = pos
        self._current_rect = QRectF(pos, pos)
    
    def on_mouse_move(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._start_pos:
            self._current_rect = QRectF(self._start_pos, pos).normalized()
            canvas.update()
    
    def on_mouse_release(
        self,
        pos: QPointF,
        canvas: "EditorCanvas",
        modifiers: Qt.KeyboardModifier
    ) -> None:
        if self._current_rect and self._current_rect.width() > 5 and self._current_rect.height() > 5:
            # Perform OCR on the selected region
            canvas.perform_ocr(self._current_rect.toRect())
        
        self._current_rect = None
        self._start_pos = None
        canvas.update()
    
    def paint_preview(self, painter: QPainter) -> None:
        """Paint the selection preview rectangle with OCR styling."""
        if self._current_rect:
            # Draw selection with blue dashed border (OCR style)
            pen = QPen(QColor(100, 150, 255, 200))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(100, 150, 255, 40))
            painter.drawRect(self._current_rect)
    
    def on_deactivate(self, canvas: "EditorCanvas") -> None:
        self._current_rect = None
        self._start_pos = None
