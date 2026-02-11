"""
Editor widget for UbShot - the main editor UI component.

This widget composes the complete editor interface:
- Top toolbar with tool buttons
- Center canvas for image display and annotation
- Right properties panel for styling
- Bottom status bar with zoom and dimensions

Layout mimics Shottr's editor UX for familiarity.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.editor.annotations import AnnotationStyle, AnnotationType
from src.editor.editor_canvas import EditorCanvas
from src.editor.tools import PointerTool, ToolBase, ToolType, create_tool
from src.services.config_service import ConfigService
from src.services.logging_service import get_logger


class ColorButton(QPushButton):
    """Button that shows a color and opens color picker on click."""
    
    color_changed = Signal(QColor)
    
    def __init__(self, color: QColor = QColor(255, 80, 80), parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(32, 32)
        self.clicked.connect(self._on_click)
        self._update_style()
    
    @property
    def color(self) -> QColor:
        return self._color
    
    @color.setter
    def color(self, value: QColor) -> None:
        self._color = value
        self._update_style()
    
    def _update_style(self) -> None:
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self._color.name()};
                border: 2px solid #555;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                border-color: #888;
            }}
        """)
    
    def _on_click(self) -> None:
        color = QColorDialog.getColor(self._color, self, "Select Color")
        if color.isValid():
            self._color = color
            self._update_style()
            self.color_changed.emit(color)


class PropertiesPanel(QFrame):
    """
    Right panel for annotation properties - shows tool-specific options.
    
    Dynamically shows/hides controls based on the current tool.
    """
    
    style_changed = Signal(AnnotationStyle)
    intensity_changed = Signal(int)  # For blur tool
    
    # Define which controls each tool needs (empty = hide panel)
    TOOL_CONFIG = {
        ToolType.POINTER: [],
        ToolType.RECTANGLE: ['stroke_width', 'stroke_color', 'fill_color'],
        ToolType.ELLIPSE: ['stroke_width', 'stroke_color', 'fill_color'],
        ToolType.ARROW: ['stroke_width', 'stroke_color', 'arrow_size'],
        ToolType.TEXT: ['font_size', 'stroke_color'],
        ToolType.FREEHAND: ['stroke_width', 'stroke_color'],
        ToolType.HIGHLIGHTER: ['stroke_width', 'stroke_color'],
        ToolType.SPOTLIGHT: [],  # No config needed
        ToolType.STEP: [],  # Fixed red color
        ToolType.BLUR: [],  # No config needed
        ToolType.ERASER: [],
    }
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = AnnotationStyle()
        self._updating = False
        self._current_tool = ToolType.POINTER
        self._intensity = 35  # Blur intensity
        
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setFixedWidth(180)
        self.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-left: 1px solid #3a3a3a;
            }
            QLabel {
                color: #ddd;
                font-size: 11px;
            }
            QSpinBox, QComboBox {
                background-color: #3a3a3a;
                color: #ddd;
                border: 1px solid #555;
                padding: 4px;
            }
        """)
        
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(12)
        
        # Title
        self._title = QLabel("Properties")
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._layout.addWidget(self._title)
        
        # Create all controls (will show/hide based on tool)
        self._controls = {}
        
        # Stroke width
        self._stroke_width_label = QLabel("Stroke Width")
        self._stroke_width = QSpinBox()
        self._stroke_width.setRange(1, 50)
        self._stroke_width.setValue(self._style.stroke_width)
        self._stroke_width.valueChanged.connect(self._on_stroke_width_changed)
        self._layout.addWidget(self._stroke_width_label)
        self._layout.addWidget(self._stroke_width)
        self._controls['stroke_width'] = (self._stroke_width_label, self._stroke_width)
        
        # Stroke color
        self._stroke_color_label = QLabel("Color")
        self._stroke_color = ColorButton(self._style.stroke_color)
        self._stroke_color.color_changed.connect(self._on_stroke_color_changed)
        self._layout.addWidget(self._stroke_color_label)
        self._layout.addWidget(self._stroke_color)
        self._controls['stroke_color'] = (self._stroke_color_label, self._stroke_color)
        
        # Fill color
        self._fill_color_label = QLabel("Fill Color")
        self._fill_container = QWidget()
        fill_row = QHBoxLayout(self._fill_container)
        fill_row.setContentsMargins(0, 0, 0, 0)
        self._fill_enabled = QPushButton("None")
        self._fill_enabled.setCheckable(True)
        self._fill_enabled.setFixedWidth(50)
        self._fill_enabled.clicked.connect(self._on_fill_toggle)
        self._fill_color = ColorButton(QColor(100, 100, 255, 80))
        self._fill_color.setEnabled(False)
        self._fill_color.color_changed.connect(self._on_fill_color_changed)
        fill_row.addWidget(self._fill_enabled)
        fill_row.addWidget(self._fill_color)
        self._layout.addWidget(self._fill_color_label)
        self._layout.addWidget(self._fill_container)
        self._controls['fill_color'] = (self._fill_color_label, self._fill_container)
        
        # Opacity
        self._opacity_label = QLabel("Opacity")
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(10, 100)
        self._opacity.setValue(int(self._style.opacity * 100))
        self._opacity.valueChanged.connect(self._on_opacity_changed)
        self._layout.addWidget(self._opacity_label)
        self._layout.addWidget(self._opacity)
        self._controls['opacity'] = (self._opacity_label, self._opacity)
        
        # Font size
        self._font_size_label = QLabel("Font Size")
        self._font_size = QSpinBox()
        self._font_size.setRange(12, 72)
        self._font_size.setValue(25)  # Default 25
        self._font_size.valueChanged.connect(self._on_font_size_changed)
        self._layout.addWidget(self._font_size_label)
        self._layout.addWidget(self._font_size)
        self._controls['font_size'] = (self._font_size_label, self._font_size)
        
        # Arrow size
        self._arrow_size_label = QLabel("Arrow Size")
        self._arrow_size = QSpinBox()
        self._arrow_size.setRange(8, 30)
        self._arrow_size.setValue(self._style.arrowhead_size)
        self._arrow_size.valueChanged.connect(self._on_arrow_size_changed)
        self._layout.addWidget(self._arrow_size_label)
        self._layout.addWidget(self._arrow_size)
        self._controls['arrow_size'] = (self._arrow_size_label, self._arrow_size)
        
        # Blur intensity
        self._intensity_label = QLabel("Blur Intensity")
        self._intensity_spin = QSpinBox()
        self._intensity_spin.setRange(10, 60)
        self._intensity_spin.setValue(35)
        self._intensity_spin.valueChanged.connect(self._on_intensity_changed)
        self._layout.addWidget(self._intensity_label)
        self._layout.addWidget(self._intensity_spin)
        self._controls['intensity'] = (self._intensity_label, self._intensity_spin)
        
        # Spacer
        self._layout.addStretch()
        
        # Initially hide all
        self._update_visible_controls()
    
    def set_tool(self, tool_type: ToolType) -> None:
        """Update panel to show controls for the given tool."""
        self._current_tool = tool_type
        self._update_visible_controls()
    
    def _update_visible_controls(self) -> None:
        """Show/hide controls based on current tool."""
        needed = self.TOOL_CONFIG.get(self._current_tool, [])
        
        # Show/hide the entire panel if no controls needed
        if not needed:
            self.hide()
            return
        else:
            self.show()
        
        # Show/hide individual controls
        for name, widgets in self._controls.items():
            visible = name in needed
            for widget in widgets:
                widget.setVisible(visible)
    
    @property
    def style(self) -> AnnotationStyle:
        return self._style
    
    def set_style(self, style: AnnotationStyle) -> None:
        """Update panel to show given style."""
        self._updating = True
        self._style = style.clone()
        
        self._stroke_width.setValue(style.stroke_width)
        self._stroke_color.color = style.stroke_color
        self._opacity.setValue(int(style.opacity * 100))
        self._font_size.setValue(style.font_size)
        self._arrow_size.setValue(style.arrowhead_size)
        
        if style.fill_color:
            self._fill_enabled.setChecked(True)
            self._fill_enabled.setText("On")
            self._fill_color.setEnabled(True)
            self._fill_color.color = style.fill_color
        else:
            self._fill_enabled.setChecked(False)
            self._fill_enabled.setText("None")
            self._fill_color.setEnabled(False)
        
        self._updating = False
    
    def update_from_style(self, style: AnnotationStyle) -> None:
        """Update panel controls to show given style."""
        self.set_style(style)
    
    def _emit_change(self) -> None:
        if not self._updating:
            self.style_changed.emit(self._style)
    
    def _on_stroke_width_changed(self, value: int) -> None:
        self._style.stroke_width = value
        self._emit_change()
    
    def _on_stroke_color_changed(self, color: QColor) -> None:
        self._style.stroke_color = color
        self._emit_change()
    
    def _on_fill_toggle(self, checked: bool) -> None:
        if checked:
            self._fill_enabled.setText("On")
            self._fill_color.setEnabled(True)
            self._style.fill_color = self._fill_color.color
        else:
            self._fill_enabled.setText("None")
            self._fill_color.setEnabled(False)
            self._style.fill_color = None
        self._emit_change()
    
    def _on_fill_color_changed(self, color: QColor) -> None:
        self._style.fill_color = color
        self._emit_change()
    
    def _on_opacity_changed(self, value: int) -> None:
        self._style.opacity = value / 100.0
        self._emit_change()
    
    def _on_font_size_changed(self, value: int) -> None:
        self._style.font_size = value
        self._emit_change()
    
    def _on_arrow_size_changed(self, value: int) -> None:
        self._style.arrowhead_size = value
        self._emit_change()
    
    def _on_intensity_changed(self, value: int) -> None:
        self._intensity = value
        self.intensity_changed.emit(value)


class StatusBar(QFrame):
    """
    Bottom status bar showing zoom, image dimensions, and cursor position.
    """
    
    zoom_selected = Signal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setFixedHeight(32)
        self.setStyleSheet("""
            QFrame {
                background-color: #2a2a2a;
                border-top: 1px solid #3a3a3a;
            }
            QLabel {
                color: #aaa;
                font-size: 11px;
            }
            QComboBox {
                background-color: #3a3a3a;
                color: #ddd;
                border: 1px solid #555;
                padding: 2px 8px;
                min-width: 70px;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(20)
        
        # Zoom control
        zoom_layout = QHBoxLayout()
        zoom_layout.setSpacing(6)
        zoom_layout.addWidget(QLabel("Zoom:"))
        
        self._zoom_combo = QComboBox()
        self._zoom_combo.addItems(["50%", "75%", "100%", "125%", "150%", "200%", "Fit"])
        self._zoom_combo.setCurrentText("100%")
        self._zoom_combo.currentTextChanged.connect(self._on_zoom_selected)
        zoom_layout.addWidget(self._zoom_combo)
        
        layout.addLayout(zoom_layout)
        
        # Dimensions
        self._dimensions = QLabel("0 × 0")
        layout.addWidget(self._dimensions)
        
        # Cursor position (optional)
        self._cursor_pos = QLabel("")
        layout.addWidget(self._cursor_pos)
        
        layout.addStretch()
    
    def set_zoom(self, zoom: float) -> None:
        """Update zoom display."""
        self._zoom_combo.blockSignals(True)
        percent = int(zoom * 100)
        text = f"{percent}%"
        
        # Select predefined if matches
        index = self._zoom_combo.findText(text)
        if index >= 0:
            self._zoom_combo.setCurrentIndex(index)
        else:
            self._zoom_combo.setEditText(text)
        
        self._zoom_combo.blockSignals(False)
    
    def set_dimensions(self, width: int, height: int) -> None:
        """Update image dimensions display."""
        self._dimensions.setText(f"{width} × {height}")
    
    def set_cursor_position(self, x: int, y: int) -> None:
        """Update cursor position display."""
        self._cursor_pos.setText(f"({x}, {y})")
    
    def _on_zoom_selected(self, text: str) -> None:
        if text == "Fit":
            self.zoom_selected.emit(-1)  # Special value for fit
        else:
            try:
                percent = int(text.replace("%", ""))
                self.zoom_selected.emit(percent / 100.0)
            except ValueError:
                pass

def _load_icon_from_file(name: str, size: int = 24) -> Optional[QIcon]:
    """Load an SVG icon from the resources/icons directory."""
    from pathlib import Path
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QSize
    
    icon_path = Path(__file__).parent.parent / "resources" / "icons" / f"{name}.svg"
    if icon_path.exists():
        renderer = QSvgRenderer(str(icon_path))
        if renderer.isValid():
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return QIcon(pixmap)
    return None


def _create_tool_icon(shape: str, color: QColor = QColor(220, 220, 220)) -> QIcon:
    """Create a tool icon - loads from file if available, otherwise draws programmatically."""
    base_size = 24
    
    # Per-icon size adjustments
    size_adjustments = {
        'text': 0.85,       # 15% smaller
        'pointer': 0.85,    # 15% smaller
        'rectangle': 1.15,  # 15% larger
        'ellipse': 1.15,    # 15% larger
        'arrow': 1.15,      # 15% larger
    }
    
    multiplier = size_adjustments.get(shape, 1.0)
    size = int(base_size * multiplier)
    
    # Check if we have a custom SVG icon for this shape
    custom_icon = _load_icon_from_file(shape, size)
    if custom_icon:
        return custom_icon
    
    # Otherwise draw programmatically
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(color)
    
    margin = 4
    
    if shape == "pointer":
        # Arrow cursor shape
        painter.setBrush(color)
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint
        points = [
            QPoint(6, 4),
            QPoint(6, 18),
            QPoint(10, 14),
            QPoint(14, 20),
            QPoint(16, 18),
            QPoint(12, 12),
            QPoint(18, 12),
        ]
        painter.drawPolygon(QPolygon(points))
    
    elif shape == "rectangle":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(margin, margin, size - margin * 2, size - margin * 2)
    
    elif shape == "ellipse":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(margin, margin, size - margin * 2, size - margin * 2)
    
    elif shape == "arrow":
        # Line with arrowhead
        painter.drawLine(6, 18, 18, 6)
        painter.setBrush(color)
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint
        points = [QPoint(18, 6), QPoint(14, 6), QPoint(18, 10)]
        painter.drawPolygon(QPolygon(points))
    
    elif shape == "text":
        painter.setFont(painter.font())
        font = painter.font()
        font.setPixelSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    
    elif shape == "crop":
        # Crop corners
        painter.drawLine(4, 4, 10, 4)
        painter.drawLine(4, 4, 4, 10)
        painter.drawLine(14, 4, 20, 4)
        painter.drawLine(20, 4, 20, 10)
        painter.drawLine(4, 14, 4, 20)
        painter.drawLine(4, 20, 10, 20)
        painter.drawLine(14, 20, 20, 20)
        painter.drawLine(20, 14, 20, 20)
    
    elif shape == "save":
        # Floppy disk / save icon
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(4, 4, 16, 16)
        painter.drawRect(7, 4, 10, 6)
        painter.drawRect(7, 12, 10, 6)
    
    elif shape == "freehand":
        # Squiggly line
        painter.setBrush(Qt.BrushStyle.NoBrush)
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.moveTo(4, 12)
        path.cubicTo(8, 4, 12, 20, 16, 10)
        path.lineTo(20, 8)
        painter.drawPath(path)
    
    elif shape == "highlighter":
        # Thick marker line
        from PySide6.QtGui import QPen
        thick_pen = QPen(QColor(255, 255, 100), 6)
        thick_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(thick_pen)
        painter.drawLine(4, 16, 20, 8)
    
    elif shape == "spotlight":
        # Circle with rays
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(6, 6, 12, 12)
        painter.drawLine(12, 2, 12, 5)
        painter.drawLine(12, 19, 12, 22)
        painter.drawLine(2, 12, 5, 12)
        painter.drawLine(19, 12, 22, 12)
    
    elif shape == "blur":
        # Pixelated square
        for i in range(3):
            for j in range(3):
                if (i + j) % 2 == 0:
                    painter.fillRect(4 + i * 6, 4 + j * 6, 5, 5, color)
    
    elif shape == "step":
        # Numbered circle
        painter.setBrush(color)
        painter.drawEllipse(4, 4, 16, 16)
        painter.setPen(QColor(40, 40, 40))
        font = painter.font()
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "1")
    
    elif shape == "eraser":
        # Eraser shape
        painter.setBrush(color)
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint
        points = [QPoint(4, 18), QPoint(10, 6), QPoint(20, 10), QPoint(14, 22)]
        painter.drawPolygon(QPolygon(points))
    
    elif shape == "eyedropper":
        # Pipette
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(6, 18, 12, 12)
        painter.drawEllipse(10, 4, 10, 10)
    
    elif shape == "ruler":
        # Ruler with marks
        painter.drawLine(4, 18, 20, 6)
        painter.drawLine(4, 18, 7, 15)
        painter.drawLine(20, 6, 17, 9)
        # Tick marks
        painter.drawLine(8, 14, 9, 13)
        painter.drawLine(12, 12, 13, 11)
        painter.drawLine(16, 10, 17, 9)
    
    painter.end()
    return QIcon(pixmap)


class EditorWidget(QWidget):
    """
    Main editor widget composing toolbar, canvas, properties, and status bar.
    
    This is the complete Shottr-like editor UI.
    """
    
    def __init__(self, config_service: Optional[ConfigService] = None, parent=None):
        super().__init__(parent)
        self._logger = get_logger(__name__)
        self._config = config_service
        
        self._tools: dict = {}
        self._current_tool_type: ToolType = ToolType.POINTER
        
        self._setup_ui()
        self._connect_signals()
        self._setup_shortcuts()
        
        # Select pointer tool by default
        self._select_tool(ToolType.POINTER)
    
    def _setup_ui(self) -> None:
        """Build the UI layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # ─── Top Toolbar ──────────────────────────────────────────────
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._toolbar.setStyleSheet("""
            QToolBar {
                background-color: #2a2a2a;
                border-bottom: 1px solid #3a3a3a;
                padding: 6px 8px;
                spacing: 4px;
            }
            QToolBar::separator {
                background-color: #444;
                width: 1px;
                margin: 4px 6px;
            }
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 6px 8px;
                margin: 2px;
                min-width: 32px;
                min-height: 32px;
            }
            QToolButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QToolButton:pressed {
                background-color: rgba(255, 255, 255, 0.15);
            }
            QToolButton:checked {
                background-color: rgba(74, 144, 226, 0.3);
            }
            QToolButton:checked:hover {
                background-color: rgba(74, 144, 226, 0.4);
            }
        """)
        
        # Tool buttons
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        
        tool_configs = [
            (ToolType.POINTER, "Pointer", "pointer", "V"),
            (ToolType.RECTANGLE, "Rectangle", "rectangle", "R"),
            (ToolType.ELLIPSE, "Ellipse", "ellipse", "E"),
            (ToolType.ARROW, "Arrow", "arrow", "A"),
            (ToolType.TEXT, "Text", "text", "T"),
            # Phase 3 tools
            (ToolType.FREEHAND, "Freehand", "freehand", "F"),
            (ToolType.HIGHLIGHTER, "Highlighter", "highlighter", "H"),
            (ToolType.SPOTLIGHT, "Spotlight", "spotlight", "S"),
            (ToolType.STEP, "Step", "step", "N"),
            (ToolType.BLUR, "Blur", "blur", "B"),
            (ToolType.ERASER, "Eraser", "eraser", "X"),
            (ToolType.OCR, "OCR (Text Recognition)", "ocr", "O"),
        ]
        
        for tool_type, tooltip, icon_shape, shortcut in tool_configs:
            btn = QToolButton()
            btn.setIcon(_create_tool_icon(icon_shape))
            btn.setToolTip(f"{tooltip} ({shortcut})")
            btn.setCheckable(True)
            btn.setProperty("tool_type", tool_type)
            btn.clicked.connect(lambda checked, t=tool_type: self._select_tool(t))
            self._tool_group.addButton(btn)
            self._toolbar.addWidget(btn)
            
            if tool_type == ToolType.POINTER:
                btn.setChecked(True)
        
        self._toolbar.addSeparator()
        
        # Apply crop button - shown when there's a crop selection
        self._apply_crop_btn = QToolButton()
        self._apply_crop_btn.setText("✓ Apply Crop")
        self._apply_crop_btn.setToolTip("Apply crop (Enter)")
        self._apply_crop_btn.setVisible(False)
        self._apply_crop_btn.setStyleSheet("""
            QToolButton {
                background-color: rgba(74, 180, 74, 0.8);
                color: white;
                padding: 6px 12px;
                border-radius: 8px;
                font-weight: 500;
            }
            QToolButton:hover {
                background-color: rgba(74, 180, 74, 1.0);
            }
            QToolButton:pressed {
                background-color: rgba(64, 160, 64, 1.0);
            }
        """)
        self._apply_crop_btn.clicked.connect(self._apply_crop)
        self._toolbar.addWidget(self._apply_crop_btn)
        
        # Cancel crop button
        self._cancel_crop_btn = QToolButton()
        self._cancel_crop_btn.setText("✗")
        self._cancel_crop_btn.setToolTip("Cancel crop (Esc)")
        self._cancel_crop_btn.setVisible(False)
        self._cancel_crop_btn.clicked.connect(self._cancel_crop)
        self._toolbar.addWidget(self._cancel_crop_btn)
        
        self._toolbar.addSeparator()
        
        # Undo/Redo
        self._undo_btn = QToolButton()
        self._undo_btn.setIcon(_create_tool_icon("undo"))
        self._undo_btn.setToolTip("Undo (Ctrl+Z)")
        self._undo_btn.clicked.connect(lambda: self._canvas.undo())
        self._toolbar.addWidget(self._undo_btn)
        
        self._redo_btn = QToolButton()
        self._redo_btn.setIcon(_create_tool_icon("redo"))
        self._redo_btn.setToolTip("Redo (Ctrl+Shift+Z)")
        self._redo_btn.clicked.connect(lambda: self._canvas.redo())
        self._toolbar.addWidget(self._redo_btn)
        
        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._toolbar.addWidget(spacer)
        
        # Copy button (copies to clipboard without closing)
        copy_btn = QToolButton()
        copy_btn.setIcon(_create_tool_icon("copy"))
        copy_btn.setToolTip("Copy to Clipboard (Ctrl+C)")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        self._toolbar.addWidget(copy_btn)
        
        # Save button
        save_btn = QToolButton()
        save_btn.setIcon(_create_tool_icon("save"))
        save_btn.setToolTip("Save (Ctrl+S)")
        save_btn.clicked.connect(self._save_image)
        self._toolbar.addWidget(save_btn)
        
        main_layout.addWidget(self._toolbar)
        
        # ─── Center Content ───────────────────────────────────────────
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        
        # Canvas
        self._canvas = EditorCanvas()
        content.addWidget(self._canvas, 1)
        
        # Properties panel
        self._properties = PropertiesPanel()
        content.addWidget(self._properties)
        
        main_layout.addLayout(content, 1)
        
        # ─── Bottom Status Bar ────────────────────────────────────────
        self._status = StatusBar()
        main_layout.addWidget(self._status)
    
    def _connect_signals(self) -> None:
        """Connect widget signals."""
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.selection_changed.connect(self._on_selection_changed)
        self._canvas.image_changed.connect(self._on_image_changed)
        self._canvas.crop_selection_changed.connect(self._on_crop_selection_changed)
        self._canvas.text_edit_finished.connect(self._on_text_edit_finished)
        self._canvas.ocr_completed.connect(self._on_ocr_completed)
        self._properties.style_changed.connect(self._on_style_changed)
        self._status.zoom_selected.connect(self._on_zoom_selected)
    
    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        # Tool shortcuts are handled in keyPressEvent
        pass
    
    # ─── Tool Management ──────────────────────────────────────────────────
    
    def _select_tool(self, tool_type: ToolType) -> None:
        """Select a tool by type."""
        self._current_tool_type = tool_type
        
        # Create or get tool
        if tool_type not in self._tools:
            self._tools[tool_type] = create_tool(tool_type)
        
        tool = self._tools[tool_type]
        
        # Update properties panel to show tool-specific controls
        self._properties.set_tool(tool_type)
        self._properties.update_from_style(tool.style)
        
        self._canvas.set_tool(tool)
        
        # Update button states
        for btn in self._tool_group.buttons():
            if btn.property("tool_type") == tool_type:
                btn.setChecked(True)
                break
        
        # Exit crop mode when changing tools  
        active_tool = self._canvas.active_tool
        if isinstance(active_tool, PointerTool):
            active_tool.clear_crop()
            self._apply_crop_btn.setVisible(False)
            self._cancel_crop_btn.setVisible(False)
    
    def _on_crop_selection_changed(self, has_selection: bool) -> None:
        """Handle crop selection change."""
        self._apply_crop_btn.setVisible(has_selection)
        self._cancel_crop_btn.setVisible(has_selection)
    
    def _cancel_crop(self) -> None:
        """Cancel the current crop selection."""
        tool = self._canvas.active_tool
        if isinstance(tool, PointerTool):
            tool.clear_crop()
            self._canvas.update()
        self._apply_crop_btn.setVisible(False)
        self._cancel_crop_btn.setVisible(False)
    
    def _apply_crop(self) -> None:
        """Apply the current crop selection."""
        tool = self._canvas.active_tool
        if isinstance(tool, PointerTool):
            tool.apply_crop(self._canvas)
    
    # ─── Signal Handlers ──────────────────────────────────────────────────
    
    @Slot(float)
    def _on_zoom_changed(self, zoom: float) -> None:
        self._status.set_zoom(zoom)
    
    @Slot()
    def _on_text_edit_finished(self) -> None:
        """Switch to Pointer tool after text editing is done."""
        self._logger.info("Received text_edit_finished signal - switching to Pointer")
        self._select_tool(ToolType.POINTER)
    
    @Slot(str)
    def _on_ocr_completed(self, text: str) -> None:
        """Handle OCR completion - copy text to clipboard and show popup."""
        from PySide6.QtWidgets import QApplication
        
        if text:
            # Copy text to clipboard
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self._logger.info(f"OCR: Copied {len(text)} characters to clipboard")
            
            # Show OCR result popup
            self._show_ocr_popup(text)
        else:
            self._logger.info("OCR: No text detected in selected region")
            self._show_ocr_popup("")
        
        # Switch back to Pointer tool
        self._select_tool(ToolType.POINTER)
    
    def _show_ocr_popup(self, text: str) -> None:
        """Show a popup with OCR results."""
        from PySide6.QtWidgets import QLabel, QPushButton
        from PySide6.QtCore import QTimer
        
        # Create popup widget
        popup = QWidget(self)
        popup.setObjectName("OCRPopup")
        popup.setStyleSheet("""
            #OCRPopup {
                background-color: rgba(40, 40, 45, 0.95);
                border: 1px solid rgba(100, 150, 255, 0.6);
                border-radius: 8px;
            }
            QLabel#OCRTitle {
                color: rgba(100, 150, 255, 1.0);
                font-weight: bold;
                font-size: 12px;
            }
            QLabel#OCRText {
                color: white;
                font-size: 11px;
            }
            QPushButton {
                background-color: rgba(100, 150, 255, 0.8);
                color: white;
                border: none;
                padding: 4px 12px;
                border-radius: 4px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: rgba(100, 150, 255, 1.0);
            }
        """)
        
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        
        # Title
        title = QLabel("✓ Text copied to clipboard" if text else "⚠ No text detected")
        title.setObjectName("OCRTitle")
        layout.addWidget(title)
        
        if text:
            # Show preview (max 100 chars)
            preview_text = text[:100] + ("..." if len(text) > 100 else "")
            preview = QLabel(preview_text)
            preview.setObjectName("OCRText")
            preview.setWordWrap(True)
            preview.setMaximumWidth(300)
            layout.addWidget(preview)
            
            # "Copy without line breaks" button
            copy_btn = QPushButton("Copy without line breaks")
            copy_btn.clicked.connect(lambda: self._copy_ocr_without_linebreaks(text, popup))
            layout.addWidget(copy_btn)
        
        popup.adjustSize()
        
        # Position popup near top-right of canvas
        canvas_rect = self._canvas.geometry()
        popup.move(canvas_rect.right() - popup.width() - 20, canvas_rect.top() + 20)
        popup.show()
        
        # Auto-close after 4 seconds
        QTimer.singleShot(4000, popup.close)
    
    def _copy_ocr_without_linebreaks(self, text: str, popup: QWidget) -> None:
        """Copy OCR text without line breaks."""
        from PySide6.QtWidgets import QApplication
        
        text_no_breaks = text.replace('\n', ' ').replace('  ', ' ')
        clipboard = QApplication.clipboard()
        clipboard.setText(text_no_breaks)
        self._logger.info("OCR: Copied text without line breaks")
        popup.close()
    
    def _ocr_full_image(self) -> None:
        """Perform OCR on the entire image (Ctrl+O shortcut)."""
        from src.services.ocr_service import extract_text, is_ocr_available
        from PySide6.QtWidgets import QApplication
        
        if not self._canvas.image:
            return
        
        if not is_ocr_available():
            self._logger.warning("OCR is not available")
            return
        
        self._logger.info("OCR: Running on full image...")
        
        # Extract text from full image
        text = extract_text(self._canvas.image)
        
        if text:
            # Copy to clipboard
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self._logger.info(f"OCR: Copied {len(text)} characters from full image")
            
            # Show popup
            self._show_ocr_popup(text)
        else:
            self._logger.info("OCR: No text detected in image")
            self._show_ocr_popup("")
    
    @Slot(object)
    def _on_selection_changed(self, annotation) -> None:
        if annotation:
            self._properties.set_style(annotation.style)
    
    @Slot()
    def _on_image_changed(self) -> None:
        w, h = self._canvas.image_size
        self._status.set_dimensions(w, h)
    
    @Slot(AnnotationStyle)
    def _on_style_changed(self, style: AnnotationStyle) -> None:
        # Update current tool's style
        tool = self._canvas.active_tool
        tool.style = style.clone()
        
        # Update selected annotation if any
        selected = self._canvas.selected_annotation
        if selected:
            selected.style = style.clone()
            self._canvas.update()
    
    @Slot(float)
    def _on_zoom_selected(self, zoom: float) -> None:
        if zoom < 0:
            self._canvas.zoom_to_fit()
        else:
            self._canvas.set_zoom(zoom)
    
    # ─── Image Management ─────────────────────────────────────────────────
    
    def set_image(self, image: QImage) -> None:
        """Load an image into the editor."""
        self._canvas.set_image(image)
        self._select_tool(ToolType.POINTER)
    
    def _save_image(self) -> None:
        """Save the current canvas to a file and close the window."""
        if not self._canvas.image:
            return
        
        # Get save folder
        if self._config:
            save_folder = Path(self._config.default_save_folder)
        else:
            save_folder = Path.home() / "Pictures" / "UbShot"
        
        save_folder.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ubshot_{timestamp}.png"
        filepath = save_folder / filename
        
        # Render and save
        result = self._canvas.render_to_image()
        if result.save(str(filepath), "PNG"):
            self._logger.info(f"Saved to {filepath}")
        else:
            self._logger.error(f"Failed to save to {filepath}")
        
        # Close the window (hides to tray)
        self.window().close()
    
    def _copy_to_clipboard(self) -> None:
        """Copy the current canvas to clipboard, save to file, and close window."""
        if not self._canvas.image:
            return
        
        # Render image with all annotations
        result = self._canvas.render_to_image()
        
        # Copy to clipboard
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setImage(result)
        self._logger.info("Copied edited image to clipboard")
        
        # Also save to file
        if self._config:
            save_folder = Path(self._config.default_save_folder)
        else:
            save_folder = Path.home() / "Pictures" / "UbShot"
        
        save_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ubshot_{timestamp}.png"
        filepath = save_folder / filename
        
        if result.save(str(filepath), "PNG"):
            self._logger.info(f"Also saved to {filepath}")
        
        # Close the window (hides to tray)
        self.window().close()
    
    # ─── Key Events ───────────────────────────────────────────────────────
    
    def keyPressEvent(self, event) -> None:
        """Handle keyboard shortcuts."""
        key = event.key()
        modifiers = event.modifiers()
        
        # Tool shortcuts
        tool_shortcuts = {
            Qt.Key.Key_V: ToolType.POINTER,
            Qt.Key.Key_R: ToolType.RECTANGLE,
            Qt.Key.Key_E: ToolType.ELLIPSE,
            Qt.Key.Key_A: ToolType.ARROW,
            Qt.Key.Key_T: ToolType.TEXT,
            # Phase 3 tools
            Qt.Key.Key_F: ToolType.FREEHAND,
            Qt.Key.Key_H: ToolType.HIGHLIGHTER,
            Qt.Key.Key_S: ToolType.SPOTLIGHT,
            Qt.Key.Key_N: ToolType.STEP,
            Qt.Key.Key_B: ToolType.BLUR,
            Qt.Key.Key_X: ToolType.ERASER,
            Qt.Key.Key_O: ToolType.OCR,
        }
        
        if key in tool_shortcuts and not modifiers:
            self._select_tool(tool_shortcuts[key])
            return
        
        # Save shortcut (Ctrl+S)
        if key == Qt.Key.Key_S and modifiers & Qt.KeyboardModifier.ControlModifier:
            self._save_image()
            return
        
        # Copy shortcut (Ctrl+C) - copies image WITH annotations
        if key == Qt.Key.Key_C and modifiers & Qt.KeyboardModifier.ControlModifier:
            self._copy_to_clipboard()
            return
        
        # OCR full image shortcut (Ctrl+O)
        if key == Qt.Key.Key_O and modifiers & Qt.KeyboardModifier.ControlModifier:
            self._ocr_full_image()
            return
        
        # Text annotation shortcuts
        selected = self._canvas.selected_annotation
        if selected and selected.annotation_type == AnnotationType.TEXT:
            # Ctrl+R: Randomize/toggle hand-drawn style
            if key == Qt.Key.Key_R and modifiers & Qt.KeyboardModifier.ControlModifier:
                if selected.hand_drawn:
                    selected.randomize_hand_drawn()
                else:
                    selected.toggle_hand_drawn()
                self._canvas.update()
                return
            
            # Ctrl+[ : Decrease font size
            if key == Qt.Key.Key_BracketLeft and modifiers & Qt.KeyboardModifier.ControlModifier:
                new_size = max(8, selected.style.font_size - 2)
                selected.style.font_size = new_size
                selected._cached_rect = None
                self._canvas.update()
                self._update_font_size_display()
                return
            
            # Ctrl+] : Increase font size
            if key == Qt.Key.Key_BracketRight and modifiers & Qt.KeyboardModifier.ControlModifier:
                new_size = min(72, selected.style.font_size + 2)
                selected.style.font_size = new_size
                selected._cached_rect = None
                self._canvas.update()
                self._update_font_size_display()
                return
        
        super().keyPressEvent(event)
    
    def _update_font_size_display(self) -> None:
        """Update font size display in properties panel."""
        selected = self._canvas.selected_annotation
        if selected and hasattr(self, '_font_size_spin'):
            self._font_size_spin.blockSignals(True)
            self._font_size_spin.setValue(selected.style.font_size)
            self._font_size_spin.blockSignals(False)

