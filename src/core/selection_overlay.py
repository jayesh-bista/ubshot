"""
Selection overlay for area capture in UbShot.

This module provides a fullscreen overlay widget that allows users to select
a rectangular region for screenshot capture with resize/move support.

Workflow:
1. Capture the entire screen(s) FIRST (before showing overlay)
2. Show the overlay with the captured image as background
3. Dim the background and let user drag-select a region (DRAWING phase)
4. User can resize handles, move, or nudge the selection (ADJUSTING phase)
5. User confirms with Enter/double-click → crop and emit

State Machine:
    IDLE → DRAWING (mouse down) → ADJUSTING (mouse up with valid rect)
    ADJUSTING → DRAWING (click outside selection to re-draw)
    ADJUSTING → capture_completed (Enter / double-click)
    Any → capture_cancelled (Escape / right-click)
"""

from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal, QSize
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from src.services.logging_service import get_logger


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class _Phase(Enum):
    """Selection overlay state machine phases."""
    IDLE = auto()       # No selection yet
    DRAWING = auto()    # Dragging to create initial selection
    ADJUSTING = auto()  # Selection exists, user can resize/move/nudge


class _HandleId(Enum):
    """Identifies which resize handle the user is interacting with."""
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


# Cursor shape for each handle
_HANDLE_CURSORS: dict[_HandleId, Qt.CursorShape] = {
    _HandleId.TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
    _HandleId.TOP: Qt.CursorShape.SizeVerCursor,
    _HandleId.TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
    _HandleId.RIGHT: Qt.CursorShape.SizeHorCursor,
    _HandleId.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
    _HandleId.BOTTOM: Qt.CursorShape.SizeVerCursor,
    _HandleId.BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
    _HandleId.LEFT: Qt.CursorShape.SizeHorCursor,
}


# ---------------------------------------------------------------------------
# Main Widget
# ---------------------------------------------------------------------------

class SelectionOverlay(QWidget):
    """
    Fullscreen overlay widget for area selection with resize/move support.

    Displays a pre-captured screenshot with a dimmed overlay. User can
    drag-select a rectangular region, then resize or move it before
    confirming with Enter.

    Signals:
        capture_completed: Emitted with QImage when capture is confirmed.
        capture_cancelled: Emitted when user cancels (ESC / right-click).
    """

    capture_completed = Signal(QImage)
    capture_cancelled = Signal()

    # ---- appearance constants ----
    DIM_COLOR = QColor(0, 0, 0, 100)
    SELECTION_BORDER_COLOR = QColor(80, 160, 255)
    SELECTION_BORDER_WIDTH = 2

    HANDLE_RADIUS = 5          # visual radius of resize circles
    HANDLE_HIT_RADIUS = 10     # hit-area radius (larger for easier grab)
    HANDLE_FILL = QColor(255, 255, 255)
    HANDLE_BORDER = QColor(80, 160, 255)
    HANDLE_HOVER_FILL = QColor(80, 160, 255)

    GUIDE_COLOR = QColor(80, 160, 255, 60)

    LABEL_BG = QColor(0, 0, 0, 180)
    LABEL_FG = QColor(255, 255, 255)
    LABEL_FONT_SIZE = 12
    LABEL_PADDING_H = 10
    LABEL_PADDING_V = 5
    LABEL_RADIUS = 6

    NUDGE_SMALL = 1
    NUDGE_LARGE = 10
    MIN_SELECTION = 5          # minimum width/height to count as valid

    # ------------------------------------------------------------------ init
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)

        # Pre-captured screenshot
        self._background_pixmap: Optional[QPixmap] = None
        self._background_image: Optional[QImage] = None

        # State
        self._phase: _Phase = _Phase.IDLE
        self._selection_rect: Optional[QRect] = None

        # Drawing phase helpers
        self._draw_origin: Optional[QPoint] = None

        # Adjusting phase helpers
        self._active_handle: Optional[_HandleId] = None
        self._hovered_handle: Optional[_HandleId] = None
        self._moving: bool = False
        self._move_origin: Optional[QPoint] = None

        # Geometry
        self._geometry: QRect = QRect()

        self._setup_window()

    # ------------------------------------------------------------ window setup
    def _setup_window(self) -> None:
        """Configure overlay window properties."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # --------------------------------------------------------- public API
    def set_screenshot(self, pixmap: QPixmap, geometry: QRect) -> None:
        """Set the pre-captured screenshot to display."""
        self._background_pixmap = pixmap
        self._background_image = pixmap.toImage()
        self._geometry = geometry

    def start_selection(self) -> None:
        """Show the overlay and begin the selection flow."""
        if not self._background_pixmap:
            self._logger.error("No screenshot set, cannot start selection!")
            self._cancel()
            return

        self.setGeometry(self._geometry)
        self._reset_state()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabMouse()
        self.grabKeyboard()
        self._logger.info(f"Selection overlay shown, geometry: {self._geometry}")

    # ------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:
        """Paint background, dim, selection, handles, dimension label."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Background screenshot
        if self._background_pixmap:
            painter.drawPixmap(0, 0, self._background_pixmap)

        # 2. Dim overlay
        painter.fillRect(self.rect(), self.DIM_COLOR)

        if not self._selection_rect or self._selection_rect.isNull():
            painter.end()
            return

        rect = self._selection_rect

        # 3. Clear the selection area (remove dim)
        if self._background_pixmap:
            painter.drawPixmap(rect, self._background_pixmap, rect)

        # 4. Selection border
        pen = QPen(self.SELECTION_BORDER_COLOR, self.SELECTION_BORDER_WIDTH)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # 5. Crosshair guide lines during DRAWING
        if self._phase == _Phase.DRAWING:
            self._paint_guides(painter, rect)

        # 6. Resize handles (only in ADJUSTING)
        if self._phase == _Phase.ADJUSTING:
            self._paint_handles(painter, rect)

        # 7. Dimension label
        self._paint_dimension_label(painter, rect)

        # 8. Hint text (only in ADJUSTING)
        if self._phase == _Phase.ADJUSTING:
            self._paint_hint(painter)

        painter.end()

    def _paint_guides(self, painter: QPainter, rect: QRect) -> None:
        """Draw dashed guide lines from selection edges to screen edges."""
        pen = QPen(self.GUIDE_COLOR, 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        screen = self.rect()

        # Horizontal guides from top/bottom edges
        painter.drawLine(screen.left(), rect.top(), screen.right(), rect.top())
        painter.drawLine(screen.left(), rect.bottom(), screen.right(), rect.bottom())
        # Vertical guides from left/right edges
        painter.drawLine(rect.left(), screen.top(), rect.left(), screen.bottom())
        painter.drawLine(rect.right(), screen.top(), rect.right(), screen.bottom())

    def _paint_handles(self, painter: QPainter, rect: QRect) -> None:
        """Draw 8 resize handle circles."""
        for hid, center in self._handle_positions(rect).items():
            is_hovered = hid == self._hovered_handle
            radius = self.HANDLE_RADIUS + (2 if is_hovered else 0)

            painter.setPen(QPen(self.HANDLE_BORDER, 1.5))
            fill = self.HANDLE_HOVER_FILL if is_hovered else self.HANDLE_FILL
            painter.setBrush(fill)
            painter.drawEllipse(center, radius, radius)

    def _paint_dimension_label(self, painter: QPainter, rect: QRect) -> None:
        """Draw styled W × H pill label below (or above) the selection."""
        w = abs(rect.width())
        h = abs(rect.height())
        text = f"{w} × {h}"

        font = QFont("Inter", self.LABEL_FONT_SIZE)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(text)

        pill_w = text_rect.width() + self.LABEL_PADDING_H * 2
        pill_h = text_rect.height() + self.LABEL_PADDING_V * 2

        # Position below selection, centered
        px = rect.center().x() - pill_w // 2
        py = rect.bottom() + 12

        # Flip above if too close to bottom
        if py + pill_h > self.height() - 10:
            py = rect.top() - pill_h - 12

        # Clamp to screen
        px = max(4, min(px, self.width() - pill_w - 4))
        py = max(4, min(py, self.height() - pill_h - 4))

        pill_rect = QRectF(px, py, pill_w, pill_h)

        # Draw pill background
        path = QPainterPath()
        path.addRoundedRect(pill_rect, self.LABEL_RADIUS, self.LABEL_RADIUS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.LABEL_BG)
        painter.drawPath(path)

        # Draw text
        painter.setPen(self.LABEL_FG)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_hint(self, painter: QPainter) -> None:
        """Draw a subtle hint at the bottom of the screen."""
        hint = "Enter to confirm  ·  Esc to cancel  ·  Arrows to nudge"
        font = QFont("Inter", 11)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(hint)

        pill_w = text_rect.width() + 24
        pill_h = text_rect.height() + 12
        px = self.width() // 2 - pill_w // 2
        py = self.height() - pill_h - 20

        pill = QRectF(px, py, pill_w, pill_h)
        path = QPainterPath()
        path.addRoundedRect(pill, pill_h // 2, pill_h // 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawPath(path)

        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, hint)

    # ---------------------------------------------------------- handle geometry
    def _handle_positions(self, rect: QRect) -> dict[_HandleId, QPoint]:
        """Return center points for the 8 resize handles."""
        l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
        mx = (l + r) // 2
        my = (t + b) // 2
        return {
            _HandleId.TOP_LEFT: QPoint(l, t),
            _HandleId.TOP: QPoint(mx, t),
            _HandleId.TOP_RIGHT: QPoint(r, t),
            _HandleId.RIGHT: QPoint(r, my),
            _HandleId.BOTTOM_RIGHT: QPoint(r, b),
            _HandleId.BOTTOM: QPoint(mx, b),
            _HandleId.BOTTOM_LEFT: QPoint(l, b),
            _HandleId.LEFT: QPoint(l, my),
        }

    def _handle_at(self, pos: QPoint) -> Optional[_HandleId]:
        """Return the handle under *pos*, or None."""
        if not self._selection_rect:
            return None
        for hid, center in self._handle_positions(self._selection_rect).items():
            dx = pos.x() - center.x()
            dy = pos.y() - center.y()
            if dx * dx + dy * dy <= self.HANDLE_HIT_RADIUS ** 2:
                return hid
        return None

    # ----------------------------------------------------------- mouse events
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()

        if self._phase == _Phase.IDLE:
            # Start drawing
            self._phase = _Phase.DRAWING
            self._draw_origin = pos
            self._selection_rect = QRect(pos, pos)
            self.update()
            return

        if self._phase == _Phase.ADJUSTING:
            # Check handle first
            handle = self._handle_at(pos)
            if handle is not None:
                self._active_handle = handle
                return

            # Check inside selection → move
            if self._selection_rect and self._selection_rect.contains(pos):
                self._moving = True
                self._move_origin = pos
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                return

            # Click outside → restart drawing
            self._phase = _Phase.DRAWING
            self._draw_origin = pos
            self._selection_rect = QRect(pos, pos)
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            self.update()
            return

        if self._phase == _Phase.DRAWING:
            # Shouldn't normally happen, but handle gracefully
            self._draw_origin = pos
            self._selection_rect = QRect(pos, pos)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()

        if self._phase == _Phase.DRAWING and self._draw_origin:
            self._selection_rect = QRect(self._draw_origin, pos).normalized()
            self.update()
            return

        if self._phase == _Phase.ADJUSTING:
            # Resizing via handle
            if self._active_handle is not None and self._selection_rect:
                self._resize_with_handle(self._active_handle, pos)
                self.update()
                return

            # Moving
            if self._moving and self._move_origin and self._selection_rect:
                delta = pos - self._move_origin
                moved = self._selection_rect.translated(delta)
                # Clamp to screen
                moved = self._clamp_rect(moved)
                self._selection_rect = moved
                self._move_origin = pos
                self.update()
                return

            # Hover → update cursor
            handle = self._handle_at(pos)
            if handle != self._hovered_handle:
                self._hovered_handle = handle
                self.update()

            if handle is not None:
                self.setCursor(QCursor(_HANDLE_CURSORS[handle]))
            elif self._selection_rect and self._selection_rect.contains(pos):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._phase == _Phase.DRAWING:
            # Finalize the drawn rect
            if self._selection_rect and (
                self._selection_rect.width() > self.MIN_SELECTION
                and self._selection_rect.height() > self.MIN_SELECTION
            ):
                self._phase = _Phase.ADJUSTING
                self._logger.debug(
                    f"Selection drawn: {self._selection_rect}, entering ADJUSTING"
                )
            else:
                # Too small — reset
                self._selection_rect = None
                self._phase = _Phase.IDLE
            self._draw_origin = None
            self.update()
            return

        if self._phase == _Phase.ADJUSTING:
            if self._active_handle is not None:
                # Normalize after resize (edges may have crossed)
                if self._selection_rect:
                    self._selection_rect = self._selection_rect.normalized()
                self._active_handle = None
            if self._moving:
                self._moving = False
                self._move_origin = None
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click inside selection = confirm capture."""
        if event.button() == Qt.MouseButton.LeftButton:
            if (
                self._phase == _Phase.ADJUSTING
                and self._selection_rect
                and self._selection_rect.contains(event.position().toPoint())
            ):
                self._complete_capture()

    # ---------------------------------------------------------- resize logic
    def _resize_with_handle(self, handle: _HandleId, pos: QPoint) -> None:
        """Adjust the selection rect by moving the edge/corner of *handle* to *pos*."""
        r = self._selection_rect
        if not r:
            return

        l, t, ri, b = r.left(), r.top(), r.right(), r.bottom()
        x, y = pos.x(), pos.y()

        # Clamp to screen
        x = max(0, min(x, self.width() - 1))
        y = max(0, min(y, self.height() - 1))

        if handle == _HandleId.TOP_LEFT:
            l, t = x, y
        elif handle == _HandleId.TOP:
            t = y
        elif handle == _HandleId.TOP_RIGHT:
            ri, t = x, y
        elif handle == _HandleId.RIGHT:
            ri = x
        elif handle == _HandleId.BOTTOM_RIGHT:
            ri, b = x, y
        elif handle == _HandleId.BOTTOM:
            b = y
        elif handle == _HandleId.BOTTOM_LEFT:
            l, b = x, y
        elif handle == _HandleId.LEFT:
            l = x

        self._selection_rect = QRect(QPoint(l, t), QPoint(ri, b))

    # ---------------------------------------------------------- keyboard
    def keyPressEvent(self, event) -> None:
        key = event.key()

        if key == Qt.Key.Key_Escape:
            self._cancel()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._phase == _Phase.ADJUSTING and self._selection_rect:
                self._complete_capture()
            return

        # Arrow-key nudge (only in ADJUSTING)
        if self._phase == _Phase.ADJUSTING and self._selection_rect:
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            step = self.NUDGE_LARGE if shift else self.NUDGE_SMALL
            dx, dy = 0, 0

            if key == Qt.Key.Key_Left:
                dx = -step
            elif key == Qt.Key.Key_Right:
                dx = step
            elif key == Qt.Key.Key_Up:
                dy = -step
            elif key == Qt.Key.Key_Down:
                dy = step

            if dx or dy:
                moved = self._selection_rect.translated(dx, dy)
                self._selection_rect = self._clamp_rect(moved)
                self.update()
                return

        super().keyPressEvent(event)

    # ---------------------------------------------------------- helpers
    def _clamp_rect(self, rect: QRect) -> QRect:
        """Clamp *rect* to stay within the screen bounds."""
        x = max(0, min(rect.x(), self.width() - rect.width()))
        y = max(0, min(rect.y(), self.height() - rect.height()))
        return QRect(x, y, rect.width(), rect.height())

    def _reset_state(self) -> None:
        """Reset all selection state to IDLE."""
        self._phase = _Phase.IDLE
        self._selection_rect = None
        self._draw_origin = None
        self._active_handle = None
        self._hovered_handle = None
        self._moving = False
        self._move_origin = None

    def _release_grabs(self) -> None:
        """Release mouse and keyboard grabs."""
        self.releaseMouse()
        self.releaseKeyboard()

    def _complete_capture(self) -> None:
        """Crop the selected region and emit capture_completed."""
        if not self._selection_rect or not self._background_image:
            self._cancel()
            return

        rect = self._selection_rect.normalized()
        cropped = self._background_image.copy(rect)
        self._logger.info(f"Capture confirmed: {cropped.width()}x{cropped.height()}")

        self._release_grabs()
        self.capture_completed.emit(cropped)
        self.close()

    def _cancel(self) -> None:
        """Cancel the selection and close overlay."""
        self._logger.info("Selection cancelled")
        self._release_grabs()
        self.capture_cancelled.emit()
        self.close()

    def closeEvent(self, event) -> None:
        """Ensure grabs are released on close."""
        self._release_grabs()
        super().closeEvent(event)
