"""
Selection overlay for area capture in UbShot.

This module provides a fullscreen overlay widget that allows users to select
a rectangular region for screenshot capture. The workflow is:

1. Capture the entire screen(s) FIRST (before showing overlay)
2. Show the overlay with the captured image as background
3. Dim the background and let user drag-select a region
4. User can RESIZE / MOVE the selection using handles
5. User confirms (Enter / double-click) → crop and emit
6. User cancels (ESC) → cancel capture

This approach ensures the user sees the actual screen content (frozen)
rather than the overlay itself. This mimics Shottr's area capture experience
with the addition of a resize step before confirming.
"""

from enum import Enum, auto
from typing import List, Optional, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from src.services.logging_service import get_logger


# ─── Enums ────────────────────────────────────────────────────────────────────


class _Phase(Enum):
    """State machine for the overlay."""
    IDLE = auto()       # Waiting for the user to start drawing
    DRAWING = auto()    # User is drawing the initial selection
    ADJUSTING = auto()  # Selection drawn; user can resize / move / re-draw
    RESIZING = auto()   # Actively dragging a handle
    MOVING = auto()     # Actively dragging the selection body
    REDRAWING = auto()  # Drawing a brand-new selection (replaces old one)


class _Handle(Enum):
    """The 8 resize handles around a selection rect."""
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


# Map handles → Qt cursor shapes
_HANDLE_CURSORS = {
    _Handle.TOP_LEFT:     Qt.CursorShape.SizeFDiagCursor,
    _Handle.TOP:          Qt.CursorShape.SizeVerCursor,
    _Handle.TOP_RIGHT:    Qt.CursorShape.SizeBDiagCursor,
    _Handle.RIGHT:        Qt.CursorShape.SizeHorCursor,
    _Handle.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
    _Handle.BOTTOM:       Qt.CursorShape.SizeVerCursor,
    _Handle.BOTTOM_LEFT:  Qt.CursorShape.SizeBDiagCursor,
    _Handle.LEFT:         Qt.CursorShape.SizeHorCursor,
}


class SelectionOverlay(QWidget):
    """
    Fullscreen overlay widget for area selection with resize support.

    Signals:
        capture_completed: Emitted with QImage when capture is successful.
        capture_cancelled: Emitted when user cancels (ESC key).
    """

    capture_completed = Signal(QImage)
    capture_cancelled = Signal()

    # ── Appearance constants ──────────────────────────────────────────────
    DIM_COLOR = QColor(0, 0, 0, 100)
    SELECTION_BORDER_COLOR = QColor(80, 160, 255)
    SELECTION_BORDER_WIDTH = 2

    HANDLE_SIZE = 8          # Normal handle size (px)
    HANDLE_HOVER_SIZE = 10   # Handle size on hover
    HANDLE_FILL = QColor(255, 255, 255)
    HANDLE_BORDER = QColor(80, 160, 255)
    HANDLE_HIT_MARGIN = 6   # Extra margin for easier grabbing

    BADGE_BG = QColor(0, 0, 0, 180)
    BADGE_TEXT_COLOR = QColor(255, 255, 255)
    BADGE_RADIUS = 10

    HINT_BG = QColor(0, 0, 0, 160)
    HINT_TEXT_COLOR = QColor(200, 200, 200)

    MIN_SELECTION = 10  # Minimum selection dimension (px)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)

        # Background
        self._background_pixmap: Optional[QPixmap] = None
        self._background_image: Optional[QImage] = None

        # State
        self._phase: _Phase = _Phase.IDLE
        self._selection_rect: Optional[QRect] = None

        # Drawing / re-drawing
        self._draw_start: Optional[QPoint] = None

        # Resize / move
        self._active_handle: Optional[_Handle] = None
        self._hover_handle: Optional[_Handle] = None
        self._drag_origin: Optional[QPoint] = None
        self._rect_before_drag: Optional[QRect] = None

        # Geometry
        self._geometry: QRect = QRect()

        self._setup_window()

    # ─── Window setup ─────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ─── Public API ───────────────────────────────────────────────────────

    def set_screenshot(self, pixmap: QPixmap, geometry: QRect) -> None:
        self._background_pixmap = pixmap
        self._background_image = pixmap.toImage()
        self._geometry = geometry
        self._logger.debug(f"Screenshot set: {pixmap.width()}x{pixmap.height()}")

    def start_selection(self) -> None:
        if not self._background_pixmap:
            self._logger.error("No screenshot set, cannot start selection!")
            self._cancel()
            return

        self._logger.debug("Starting area selection overlay")
        self.setGeometry(self._geometry)

        # Reset state
        self._phase = _Phase.IDLE
        self._selection_rect = None
        self._draw_start = None
        self._active_handle = None
        self._hover_handle = None

        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabMouse()
        self.grabKeyboard()

        self._logger.info(f"Selection overlay shown, geometry: {self._geometry}")

    # ─── Handle geometry helpers ──────────────────────────────────────────

    def _handle_rects(self) -> List[Tuple[_Handle, QRect]]:
        """Return (handle_enum, rect) for each of the 8 handles."""
        if not self._selection_rect:
            return []

        r = self._selection_rect
        s = self.HANDLE_SIZE
        hs = s // 2

        cx = r.center().x()
        cy = r.center().y()

        positions = {
            _Handle.TOP_LEFT:     QPoint(r.left(), r.top()),
            _Handle.TOP:          QPoint(cx, r.top()),
            _Handle.TOP_RIGHT:    QPoint(r.right(), r.top()),
            _Handle.RIGHT:        QPoint(r.right(), cy),
            _Handle.BOTTOM_RIGHT: QPoint(r.right(), r.bottom()),
            _Handle.BOTTOM:       QPoint(cx, r.bottom()),
            _Handle.BOTTOM_LEFT:  QPoint(r.left(), r.bottom()),
            _Handle.LEFT:         QPoint(r.left(), cy),
        }

        return [
            (h, QRect(p.x() - hs, p.y() - hs, s, s))
            for h, p in positions.items()
        ]

    def _hit_test(self, pos: QPoint) -> Tuple[Optional[_Handle], bool]:
        """
        Determine what the cursor is over.

        Returns:
            (handle, inside)
            - handle is not None if hovering a handle
            - inside is True if inside the selection (but not on a handle)
        """
        margin = self.HANDLE_HIT_MARGIN

        # Check handles first (they take priority)
        for handle, rect in self._handle_rects():
            expanded = rect.adjusted(-margin, -margin, margin, margin)
            if expanded.contains(pos):
                return handle, False

        # Check if inside selection
        if self._selection_rect and self._selection_rect.contains(pos):
            return None, True

        return None, False

    # ─── Paint ────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw background screenshot
        if self._background_pixmap:
            painter.drawPixmap(0, 0, self._background_pixmap)

        # Dim overlay
        painter.fillRect(self.rect(), self.DIM_COLOR)

        # Selection rect
        if self._selection_rect and not self._selection_rect.isNull():
            # Clear the selection area (show un-dimmed image)
            if self._background_pixmap:
                painter.drawPixmap(
                    self._selection_rect,
                    self._background_pixmap,
                    self._selection_rect,
                )

            # Selection border
            pen = QPen(self.SELECTION_BORDER_COLOR, self.SELECTION_BORDER_WIDTH)
            pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._selection_rect)

            # Dimension badge
            self._draw_dimension_badge(painter)

            # Resize handles (only in adjusting / idle-after-draw phases)
            if self._phase in (_Phase.ADJUSTING, _Phase.RESIZING, _Phase.MOVING):
                self._draw_handles(painter)
                self._draw_hint_bar(painter)

        painter.end()

    def _draw_handles(self, painter: QPainter) -> None:
        """Draw the 8 resize handles."""
        for handle, rect in self._handle_rects():
            size = (
                self.HANDLE_HOVER_SIZE
                if handle == self._hover_handle
                else self.HANDLE_SIZE
            )
            # Re-center if hover-enlarged
            if handle == self._hover_handle:
                center = rect.center()
                hs = size // 2
                rect = QRect(center.x() - hs, center.y() - hs, size, size)

            # Shadow
            shadow_rect = rect.adjusted(1, 1, 1, 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 80))
            painter.drawRoundedRect(shadow_rect, 2, 2)

            # Handle body
            painter.setPen(QPen(self.HANDLE_BORDER, 1.5))
            painter.setBrush(QBrush(self.HANDLE_FILL))
            painter.drawRoundedRect(rect, 2, 2)

    def _draw_dimension_badge(self, painter: QPainter) -> None:
        """Draw a pill-shaped dimension badge below the selection."""
        if not self._selection_rect:
            return

        w = abs(self._selection_rect.width())
        h = abs(self._selection_rect.height())
        text = f"{w} × {h}"

        font = QFont("Inter", 11)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(text)

        pad_x, pad_y = 14, 6
        badge_w = text_rect.width() + pad_x * 2
        badge_h = text_rect.height() + pad_y * 2

        # Position below center of selection
        bx = self._selection_rect.center().x() - badge_w // 2
        by = self._selection_rect.bottom() + 12

        # Keep on screen
        if by + badge_h > self.height() - 10:
            by = self._selection_rect.top() - badge_h - 12

        badge_rect = QRect(bx, by, badge_w, badge_h)

        # Draw badge background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.BADGE_BG))
        painter.drawRoundedRect(badge_rect, self.BADGE_RADIUS, self.BADGE_RADIUS)

        # Draw text
        painter.setPen(self.BADGE_TEXT_COLOR)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_hint_bar(self, painter: QPainter) -> None:
        """Draw action hint text below the dimension badge."""
        if not self._selection_rect:
            return

        hint = "Enter to confirm  ·  ESC to cancel  ·  Drag handles to resize"

        font = QFont("Inter", 9)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(hint)

        pad_x, pad_y = 16, 5
        bar_w = text_rect.width() + pad_x * 2
        bar_h = text_rect.height() + pad_y * 2

        # Position below the dimension badge (badge is ~12px below selection bottom)
        bx = self._selection_rect.center().x() - bar_w // 2
        by = self._selection_rect.bottom() + 48

        # If badge went above, put hint above too
        dim_badge_bottom = self._selection_rect.bottom() + 12 + 30
        if dim_badge_bottom > self.height() - 10:
            by = self._selection_rect.top() - 30 - bar_h - 8

        # Keep on screen
        if by + bar_h > self.height() - 5:
            by = self._selection_rect.top() - bar_h - 50

        bar_rect = QRect(bx, by, bar_w, bar_h)

        # Draw background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.HINT_BG))
        painter.drawRoundedRect(bar_rect, 8, 8)

        # Draw text
        painter.setPen(self.HINT_TEXT_COLOR)
        painter.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter, hint)

    # ─── Mouse events ─────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()

        if self._phase in (_Phase.IDLE, _Phase.REDRAWING):
            # Start drawing a new selection
            self._phase = _Phase.DRAWING
            self._draw_start = pos
            self._selection_rect = QRect(pos, pos)
            self.update()
            self._logger.debug(f"Selection drawing started at {pos}")

        elif self._phase == _Phase.ADJUSTING:
            handle, inside = self._hit_test(pos)

            if handle is not None:
                # Start resizing via a handle
                self._phase = _Phase.RESIZING
                self._active_handle = handle
                self._drag_origin = pos
                self._rect_before_drag = QRect(self._selection_rect)
                self._logger.debug(f"Resize started via handle {handle.name}")

            elif inside:
                # Start moving the selection
                self._phase = _Phase.MOVING
                self._drag_origin = pos
                self._rect_before_drag = QRect(self._selection_rect)
                self._logger.debug("Move started")

            else:
                # Click outside → start a brand-new selection
                self._phase = _Phase.DRAWING
                self._draw_start = pos
                self._selection_rect = QRect(pos, pos)
                self._hover_handle = None
                self.update()
                self._logger.debug(f"Re-drawing selection from {pos}")

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()

        if self._phase == _Phase.DRAWING:
            self._selection_rect = QRect(self._draw_start, pos).normalized()
            self.update()

        elif self._phase == _Phase.RESIZING:
            self._do_resize(pos)
            self.update()

        elif self._phase == _Phase.MOVING:
            self._do_move(pos)
            self.update()

        elif self._phase == _Phase.ADJUSTING:
            # Update hover handle for cursor & visual feedback
            handle, inside = self._hit_test(pos)
            old_hover = self._hover_handle
            self._hover_handle = handle

            if handle is not None:
                self.setCursor(QCursor(_HANDLE_CURSORS[handle]))
            elif inside:
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

            if old_hover != self._hover_handle:
                self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._phase == _Phase.DRAWING:
            pos = event.position().toPoint()
            self._selection_rect = QRect(self._draw_start, pos).normalized()

            if (self._selection_rect.width() > self.MIN_SELECTION
                    and self._selection_rect.height() > self.MIN_SELECTION):
                # Transition to ADJUSTING phase — user can now resize/move
                self._phase = _Phase.ADJUSTING
                self._logger.debug(
                    f"Selection drawn: {self._selection_rect}  → adjusting mode"
                )
            else:
                self._logger.warning("Selection too small, resetting to idle")
                self._selection_rect = None
                self._phase = _Phase.IDLE

            self.update()

        elif self._phase == _Phase.RESIZING:
            self._active_handle = None
            self._phase = _Phase.ADJUSTING
            self._logger.debug("Resize finished → adjusting mode")

        elif self._phase == _Phase.MOVING:
            self._phase = _Phase.ADJUSTING
            self._logger.debug("Move finished → adjusting mode")

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click inside the selection to confirm."""
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._phase in (_Phase.ADJUSTING, _Phase.IDLE):
            if (self._selection_rect
                    and self._selection_rect.contains(event.position().toPoint())):
                self._logger.info("Double-click confirm")
                self._complete_capture()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._logger.info("Selection cancelled by ESC key")
            self._cancel()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._phase == _Phase.ADJUSTING and self._selection_rect:
                self._logger.info("Selection confirmed by Enter key")
                self._complete_capture()
        else:
            super().keyPressEvent(event)

    # ─── Resize / move logic ──────────────────────────────────────────────

    def _do_resize(self, pos: QPoint) -> None:
        """Resize the selection rect based on which handle is being dragged."""
        if not self._rect_before_drag or not self._active_handle:
            return

        r = QRect(self._rect_before_drag)
        h = self._active_handle

        # Adjust the relevant edges
        if h in (_Handle.TOP_LEFT, _Handle.TOP, _Handle.TOP_RIGHT):
            r.setTop(pos.y())
        if h in (_Handle.BOTTOM_LEFT, _Handle.BOTTOM, _Handle.BOTTOM_RIGHT):
            r.setBottom(pos.y())
        if h in (_Handle.TOP_LEFT, _Handle.LEFT, _Handle.BOTTOM_LEFT):
            r.setLeft(pos.x())
        if h in (_Handle.TOP_RIGHT, _Handle.RIGHT, _Handle.BOTTOM_RIGHT):
            r.setRight(pos.x())

        # Normalize (handles flipping when dragged past opposite edge)
        r = r.normalized()

        # Enforce minimum size
        if r.width() < self.MIN_SELECTION:
            r.setWidth(self.MIN_SELECTION)
        if r.height() < self.MIN_SELECTION:
            r.setHeight(self.MIN_SELECTION)

        # Clamp to screen
        screen = self.rect()
        if r.left() < screen.left():
            r.moveLeft(screen.left())
        if r.top() < screen.top():
            r.moveTop(screen.top())
        if r.right() > screen.right():
            r.moveRight(screen.right())
        if r.bottom() > screen.bottom():
            r.moveBottom(screen.bottom())

        self._selection_rect = r

    def _do_move(self, pos: QPoint) -> None:
        """Move the entire selection rect by the drag delta."""
        if not self._rect_before_drag or not self._drag_origin:
            return

        delta = pos - self._drag_origin
        r = QRect(self._rect_before_drag)
        r.translate(delta)

        # Clamp to screen bounds
        screen = self.rect()
        if r.left() < screen.left():
            r.moveLeft(screen.left())
        if r.top() < screen.top():
            r.moveTop(screen.top())
        if r.right() > screen.right():
            r.moveRight(screen.right())
        if r.bottom() > screen.bottom():
            r.moveBottom(screen.bottom())

        self._selection_rect = r

    # ─── Capture completion / cancellation ─────────────────────────────────

    def _release_grabs(self) -> None:
        self.releaseMouse()
        self.releaseKeyboard()

    def _complete_capture(self) -> None:
        """Crop the selected region from the pre-captured image and emit."""
        if not self._selection_rect or not self._background_image:
            self._cancel()
            return

        cropped = self._background_image.copy(self._selection_rect)
        self._logger.info(
            f"Cropped image: {cropped.width()}x{cropped.height()}"
        )

        self._release_grabs()
        self.capture_completed.emit(cropped)
        self.close()

    def _cancel(self) -> None:
        self._release_grabs()
        self.capture_cancelled.emit()
        self.close()

    def closeEvent(self, event) -> None:
        self._release_grabs()
        super().closeEvent(event)
