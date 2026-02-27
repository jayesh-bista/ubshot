# UbShot

A **Shottr-like screenshot and annotation tool for Ubuntu Linux**.

![UbShot](https://raw.githubusercontent.com/bistaSananiJayesh/ubshot/main/src/resources/icons/app_icon.png)

## ✨ Features

- 📷 **Area capture** - Select a region with dimmed overlay
- 🖥️ **Fullscreen capture** - Capture entire screen
- ⚡ **Global hotkeys** - Works even when app is not focused
- 📋 **Auto-copy** - Screenshots automatically copied to clipboard
- 💾 **Auto-save** - Automatic saving to disk
- ✏️ **Annotation tools** - Rectangle, Ellipse, Arrow, Text, Freehand, Highlighter, Spotlight, Blur, Step Counter, Eraser
- 🔤 **OCR** - Extract text from screenshots (region or full image)
- ↩️ **Undo/Redo** - Full history support
- 🔔 **System tray** - Quick access to capture actions

## 🚀 Installation

### Prerequisites

```bash
# Install system dependencies
sudo apt install python3 python3-pip python3-venv tesseract-ocr tesseract-ocr-eng
```

### Run from Source

```bash
git clone https://github.com/bistaSananiJayesh/ubshot.git
cd ubshot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.app
```

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+A` | Capture Area |
| `Ctrl+Shift+S` | Capture Fullscreen |
| `Ctrl+C` | Copy to Clipboard |
| `Ctrl+S` | Save |
| `Ctrl+O` | OCR Full Image |
| `Ctrl+Z` | Undo |
| `Ctrl+Shift+Z` | Redo |
| `Delete` | Delete Selected |
| `Escape` | Close / Cancel |

## 🛠️ Tool Shortcuts

| Key | Tool |
|-----|------|
| `V` | Pointer |
| `R` | Rectangle |
| `E` | Ellipse |
| `A` | Arrow |
| `T` | Text |
| `F` | Freehand |
| `H` | Highlighter |
| `S` | Spotlight |
| `B` | Blur |
| `N` | Step Counter |
| `O` | OCR (region select) |

## 📋 Requirements

- Ubuntu 22.04+ / Debian 12+
- Python 3.10+
- X11 (for global hotkeys)
- `tesseract-ocr` (for OCR feature)

## 📄 License

MIT License - feel free to use, modify, and distribute.

## 🙏 Credits

Inspired by [Shottr](https://shottr.cc/) - the best screenshot tool for macOS.
