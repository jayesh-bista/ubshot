"""
Main window for UbShot application.

This module contains the main application window with the editor widget,
menu bar, and dark theme styling. In Phase 2, this uses the full 
EditorWidget instead of the placeholder.
"""

from typing import Optional

from PySide6.QtGui import QAction, QImage, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QWidget,
)

from src.editor.editor_widget import EditorWidget
from src.services.config_service import ConfigService
from src.services.logging_service import get_logger


class MainWindow(QMainWindow):
    """
    Main application window for UbShot.

    Features:
    - Dark themed UI
    - Menu bar with File and Help menus
    - Full featured editor widget for screenshots

    The editor includes:
    - Canvas with zoom/pan
    - Toolbar with annotation tools
    - Properties panel
    - Status bar with zoom/dimensions
    """

    def __init__(
        self,
        config_service: Optional[ConfigService] = None,
        parent: Optional[QWidget] = None
    ) -> None:
        """
        Initialize the MainWindow.

        Args:
            config_service: Optional config service for save folder.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._logger = get_logger(__name__)
        self._config = config_service
        self._editor: Optional[EditorWidget] = None

        self._setup_window()
        self._setup_central_widget()
        # Menu bar removed for cleaner UI - all functions available via toolbar
        # self._setup_menu_bar()

        self._logger.info("MainWindow initialized")

    def _setup_window(self) -> None:
        """Configure main window properties."""
        self.setWindowTitle("UbShot - Screenshot & Annotation Tool")
        self.setMinimumSize(800, 600)
        self.resize(1200, 800)

        # Center window on screen
        # TODO: Restore window geometry from config in future phases
        # TODO: Open window on the screen where screenshot was taken

    def _setup_central_widget(self) -> None:
        """Set up the central widget (editor)."""
        self._editor = EditorWidget(self._config, self)
        self.setCentralWidget(self._editor)

    def _setup_menu_bar(self) -> None:
        """Create and configure the menu bar."""
        menu_bar = self.menuBar()

        # ─── File Menu ────────────────────────────────────────────────
        file_menu = menu_bar.addMenu("&File")

        # Save action
        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.setStatusTip("Save the current screenshot")
        save_action.triggered.connect(self._on_save)
        file_menu.addAction(save_action)

        # TODO: Add Save As (Phase 2+)
        # TODO: Add Open Image (Phase 2+)
        # TODO: Add Export options (Phase 2+)

        file_menu.addSeparator()

        # Quit action
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setStatusTip("Exit the application")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # ─── Edit Menu ────────────────────────────────────────────────
        edit_menu = menu_bar.addMenu("&Edit")

        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self._on_undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self._on_redo)
        edit_menu.addAction(redo_action)

        # TODO: Add Cut, Copy, Paste in future phases

        # ─── View Menu ────────────────────────────────────────────────
        view_menu = menu_bar.addMenu("&View")

        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcut("Ctrl++")
        zoom_in_action.triggered.connect(self._on_zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcut("Ctrl+-")
        zoom_out_action.triggered.connect(self._on_zoom_out)
        view_menu.addAction(zoom_out_action)

        zoom_100_action = QAction("Zoom &100%", self)
        zoom_100_action.setShortcut("Ctrl+0")
        zoom_100_action.triggered.connect(self._on_zoom_100)
        view_menu.addAction(zoom_100_action)

        zoom_fit_action = QAction("Zoom to &Fit", self)
        zoom_fit_action.triggered.connect(self._on_zoom_fit)
        view_menu.addAction(zoom_fit_action)

        # ─── Help Menu ────────────────────────────────────────────────
        help_menu = menu_bar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.setStatusTip("About UbShot")
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    # ─── Public Methods ───────────────────────────────────────────────────

    def set_image(self, image: QImage) -> None:
        """
        Load an image into the editor.

        Args:
            image: The QImage to display and edit.
        """
        if self._editor:
            self._editor.set_image(image)
            self._update_title_for_image(image)
            self._logger.info(
                f"Image loaded in editor: {image.width()}x{image.height()}"
            )

    def load_image_in_editor(self, image: QImage) -> None:
        """
        Load an image and show the window.

        This is the main entry point for opening captured screenshots.

        Args:
            image: The captured screenshot.
        """
        self.set_image(image)
        self.show()
        self.raise_()
        self.activateWindow()

    def _update_title_for_image(self, image: QImage) -> None:
        """Update window title to show image dimensions."""
        self.setWindowTitle(
            f"UbShot - {image.width()}×{image.height()} - Screenshot & Annotation Tool"
        )

    # ─── Menu Actions ─────────────────────────────────────────────────────

    def _on_save(self) -> None:
        """Handle File > Save."""
        if self._editor:
            self._editor._save_image()

    def _on_undo(self) -> None:
        """Handle Edit > Undo."""
        if self._editor and self._editor._canvas:
            self._editor._canvas.undo()

    def _on_redo(self) -> None:
        """Handle Edit > Redo."""
        if self._editor and self._editor._canvas:
            self._editor._canvas.redo()

    def _on_zoom_in(self) -> None:
        """Handle View > Zoom In."""
        if self._editor and self._editor._canvas:
            self._editor._canvas.zoom_in()

    def _on_zoom_out(self) -> None:
        """Handle View > Zoom Out."""
        if self._editor and self._editor._canvas:
            self._editor._canvas.zoom_out()

    def _on_zoom_100(self) -> None:
        """Handle View > Zoom 100%."""
        if self._editor and self._editor._canvas:
            self._editor._canvas.zoom_to_100()

    def _on_zoom_fit(self) -> None:
        """Handle View > Zoom to Fit."""
        if self._editor and self._editor._canvas:
            self._editor._canvas.zoom_to_fit()

    def _show_about_dialog(self) -> None:
        """Display the About dialog."""
        about_text = (
            "<h2>UbShot</h2>"
            "<p>A Shottr-like Screenshot & Annotation Tool for Ubuntu</p>"
            "<p><b>Version:</b> 0.3.0 (Phase 2 - Editor MVP)</p>"
            "<hr>"
            "<p>UbShot is an open-source screenshot and annotation tool "
            "designed for Ubuntu Linux, inspired by Shottr for macOS.</p>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>Area & fullscreen capture</li>"
            "<li>System tray integration</li>"
            "<li>Annotation tools: Rectangle, Ellipse, Arrow, Text</li>"
            "<li>Zoom and pan</li>"
            "<li>Undo/redo</li>"
            "<li>Save to PNG</li>"
            "</ul>"
            "<p><b>Keyboard Shortcuts:</b></p>"
            "<ul>"
            "<li>V - Pointer tool</li>"
            "<li>R - Rectangle tool</li>"
            "<li>E - Ellipse tool</li>"
            "<li>A - Arrow tool</li>"
            "<li>T - Text tool</li>"
            "<li>Ctrl+Z - Undo</li>"
            "<li>Ctrl+S - Save</li>"
            "</ul>"
            "<hr>"
            "<p>© 2024 UbShot Project</p>"
        )

        QMessageBox.about(self, "About UbShot", about_text)

    def closeEvent(self, event) -> None:
        """Handle window close event - auto-copy edited image to clipboard."""
        self._logger.info("MainWindow closing")
        
        try:
            # Auto-copy the edited image (with annotations) to clipboard
            if hasattr(self, '_editor') and self._editor and self._editor._canvas.image:
                from PySide6.QtWidgets import QApplication
                rendered = self._editor._canvas.render_to_image()
                clipboard = QApplication.clipboard()
                clipboard.setImage(rendered)
                self._logger.info("Auto-copied edited image to clipboard")
        except Exception as e:
            self._logger.error(f"Error during auto-copy: {e}")
        
        # Just hide the window instead of quitting (app stays in tray)
        self.hide()
        event.ignore()  # Don't actually close, just hide
