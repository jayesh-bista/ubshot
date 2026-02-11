"""
Application core for UbShot.

This module contains the AppCore class which is responsible for:
- Initializing all services (config, logging, capture, tray, hotkeys)
- Creating and managing the main window
- Applying global styling (dark theme)
- Wiring together all application components
- Handling the capture-to-editor flow

This is the central orchestration point for the application.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QClipboard, QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication

from src.core.capture_service import CaptureService
from src.core.hotkey_service import HotkeyService
from src.core.tray_service import TrayService
from src.services.config_service import ConfigService
from src.services.logging_service import get_logger, setup_logging
from src.ui.main_window import MainWindow


class AppCore(QObject):
    """
    Central application core that wires together all components.

    Responsibilities:
    - Initialize all services
    - Apply global dark theme
    - Create and show the MainWindow
    - Handle capture workflow (hotkey/tray → capture → editor)
    - Manage auto-copy and auto-save based on config

    The capture flow follows Shottr's behavior:
    1. User triggers capture (hotkey or tray menu)
    2. CaptureService handles the capture (overlay for area, immediate for fullscreen)
    3. On successful capture, image is passed to editor
    4. If configured, image is also copied to clipboard and/or saved to disk
    """

    def __init__(self, app: QApplication) -> None:
        """
        Initialize the application core.

        Args:
            app: The QApplication instance.
        """
        super().__init__()
        self._app = app

        # Service references
        self._config_service: Optional[ConfigService] = None
        self._capture_service: Optional[CaptureService] = None
        self._tray_service: Optional[TrayService] = None
        self._hotkey_service: Optional[HotkeyService] = None
        self._main_window: Optional[MainWindow] = None

        # Initialize in order
        self._init_services()
        self._apply_dark_theme()
        self._init_ui()
        self._init_tray()
        self._init_hotkeys()
        self._connect_signals()

    def _init_services(self) -> None:
        """Initialize all application services."""
        # Setup logging first
        setup_logging()
        self._logger = get_logger(__name__)
        self._logger.info("Initializing UbShot application core...")

        # Initialize config service
        self._config_service = ConfigService()
        self._logger.info(f"Theme from config: {self._config_service.theme}")

        # Initialize capture service
        self._capture_service = CaptureService(self)
        self._logger.info("Capture service initialized")

    def _apply_dark_theme(self) -> None:
        """
        Apply a dark color palette to the application.

        Uses Qt's QPalette for a native-looking dark theme.
        """
        self._logger.debug("Applying dark theme...")

        palette = QPalette()

        # Window and base colors
        palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(50, 50, 50))

        # Text colors
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))

        # Button colors
        palette.setColor(QPalette.ColorRole.Button, QColor(55, 55, 55))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))

        # Highlight colors
        palette.setColor(QPalette.ColorRole.Highlight, QColor(80, 120, 180))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

        # Other elements
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(60, 60, 60))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Link, QColor(100, 150, 220))
        palette.setColor(QPalette.ColorRole.LinkVisited, QColor(150, 120, 200))

        # Disabled state colors
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor(127, 127, 127)
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor(127, 127, 127)
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor(127, 127, 127)
        )

        # Apply the palette
        self._app.setPalette(palette)

        # Additional stylesheet tweaks for better appearance
        self._app.setStyleSheet("""
            QToolTip {
                background-color: #3d3d3d;
                color: #dcdcdc;
                border: 1px solid #5a5a5a;
                padding: 4px;
            }
            QMenuBar {
                background-color: #2d2d2d;
                padding: 2px;
            }
            QMenuBar::item {
                padding: 4px 8px;
                background-color: transparent;
            }
            QMenuBar::item:selected {
                background-color: #4a4a4a;
            }
            QMenu {
                background-color: #2d2d2d;
                border: 1px solid #3a3a3a;
            }
            QMenu::item {
                padding: 6px 20px;
            }
            QMenu::item:selected {
                background-color: #4a6a9a;
            }
        """)

        self._logger.info("Dark theme applied")

    def _init_ui(self) -> None:
        """Initialize the main window (hidden by default, shown on capture)."""
        self._logger.debug("Initializing main window...")
        self._main_window = MainWindow()
        # Don't show window on startup - it will be shown when a capture is made
        # This mimics Shottr's behavior where the editor only appears after capture
        self._logger.info("Main window initialized (hidden)")

    def _init_tray(self) -> None:
        """Initialize the system tray icon and menu."""
        self._logger.debug("Initializing tray service...")
        self._tray_service = TrayService(self)
        self._tray_service.show()
        self._logger.info("Tray service initialized and shown")

    def _init_hotkeys(self) -> None:
        """Initialize global hotkey bindings."""
        self._logger.debug("Initializing hotkey service...")
        self._hotkey_service = HotkeyService(self._config_service, self)
        self._logger.info("Hotkey service initialized")

    def _connect_signals(self) -> None:
        """Connect all service signals to their handlers."""
        # Connect tray service signals
        if self._tray_service:
            self._tray_service.capture_area_requested.connect(
                self._on_capture_area_requested
            )
            self._tray_service.capture_fullscreen_requested.connect(
                self._on_capture_fullscreen_requested
            )
            self._tray_service.quit_requested.connect(self._on_quit_requested)

        # Connect hotkey service signals
        if self._hotkey_service:
            self._hotkey_service.area_capture_triggered.connect(
                self._on_capture_area_requested
            )
            self._hotkey_service.fullscreen_capture_triggered.connect(
                self._on_capture_fullscreen_requested
            )

        # Connect capture service signals
        if self._capture_service:
            self._capture_service.capture_completed.connect(
                self._on_capture_completed
            )
            self._capture_service.capture_cancelled.connect(
                self._on_capture_cancelled
            )

        self._logger.debug("All signals connected")

    # ─── Capture Flow Handlers ────────────────────────────────────────────

    @Slot()
    def _on_capture_area_requested(self) -> None:
        """Handle request to start area capture."""
        self._logger.info("Area capture requested")
        if self._capture_service:
            self._capture_service.start_area_capture()

    @Slot()
    def _on_capture_fullscreen_requested(self) -> None:
        """Handle request to start fullscreen capture."""
        self._logger.info("Fullscreen capture requested")
        if self._capture_service:
            self._capture_service.capture_fullscreen()

    @Slot(QImage)
    def _on_capture_completed(self, image: QImage) -> None:
        """
        Handle successful capture.

        This is the main capture-to-editor flow:
        1. Auto-copy to clipboard if enabled
        2. Auto-save to disk if enabled
        3. Open the editor with the captured image

        Args:
            image: The captured screenshot as QImage.
        """
        self._logger.info(
            f"Capture completed: {image.width()}x{image.height()}"
        )

        # Auto-copy to clipboard if enabled
        if self._config_service and self._config_service.auto_copy_to_clipboard:
            self._copy_to_clipboard(image)

        # Auto-save if enabled
        if self._config_service and self._config_service.auto_save:
            self._save_to_disk(image)

        # Open editor with the captured image
        self.open_editor_with_image(image)

        # Show tray notification
        if self._tray_service:
            self._tray_service.show_message(
                "Screenshot Captured",
                f"Size: {image.width()}×{image.height()}",
                duration_ms=2000
            )

    @Slot()
    def _on_capture_cancelled(self) -> None:
        """Handle capture cancellation."""
        self._logger.info("Capture cancelled by user")

    # ─── Editor Integration ───────────────────────────────────────────────

    def open_editor_with_image(self, image: QImage) -> None:
        """
        Open the editor window with the captured image.

        Args:
            image: The captured screenshot as QImage.
        """
        self._logger.debug("Opening editor with captured image")

        if self._main_window:
            # Use the new load_image_in_editor method which:
            # - Sets the image on the EditorWidget
            # - Shows the window
            # - Brings it to front
            self._main_window.load_image_in_editor(image)
            self._logger.info("Editor opened with captured image")

    # ─── Auto-Copy and Auto-Save ──────────────────────────────────────────

    def _copy_to_clipboard(self, image: QImage) -> None:
        """
        Copy the captured image to system clipboard.

        Args:
            image: The image to copy.
        """
        clipboard: QClipboard = QApplication.clipboard()
        clipboard.setImage(image)
        self._logger.info("Screenshot copied to clipboard")

    def _save_to_disk(self, image: QImage) -> None:
        """
        Save the captured image to the default save folder.

        Args:
            image: The image to save.
        """
        if not self._config_service:
            return

        save_folder = Path(self._config_service.default_save_folder)

        # Create folder if it doesn't exist
        try:
            save_folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._logger.error(f"Could not create save folder: {e}")
            return

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ubshot_{timestamp}.png"
        filepath = save_folder / filename

        # Save the image
        if image.save(str(filepath), "PNG"):
            self._logger.info(f"Screenshot saved to: {filepath}")
        else:
            self._logger.error(f"Failed to save screenshot to: {filepath}")

    # ─── Application Lifecycle ────────────────────────────────────────────

    @Slot()
    def _on_quit_requested(self) -> None:
        """Handle quit request from tray menu."""
        self._logger.info("Quit requested, shutting down...")
        self.shutdown()

    def shutdown(self) -> None:
        """Clean shutdown of all services."""
        self._logger.info("Shutting down UbShot...")

        # Stop hotkey listener
        if self._hotkey_service:
            self._hotkey_service.stop()

        # Hide tray icon
        if self._tray_service:
            self._tray_service.hide()

        # Quit the application
        QApplication.quit()

    # ─── Properties ───────────────────────────────────────────────────────

    @property
    def config(self) -> ConfigService:
        """Get the configuration service."""
        if self._config_service is None:
            raise RuntimeError("ConfigService not initialized")
        return self._config_service

    @property
    def main_window(self) -> MainWindow:
        """Get the main window."""
        if self._main_window is None:
            raise RuntimeError("MainWindow not initialized")
        return self._main_window
