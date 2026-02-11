"""
OCR Service - Provides text extraction from images using Tesseract.
"""
import logging
from typing import Optional
from PySide6.QtGui import QImage
from PySide6.QtCore import QRect, QBuffer, QIODevice

try:
    import pytesseract
    from PIL import Image
    import io
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


def is_ocr_available() -> bool:
    """Check if OCR functionality is available."""
    return OCR_AVAILABLE


def qimage_to_pil(qimage: QImage) -> Optional["Image.Image"]:
    """Convert QImage to PIL Image for pytesseract processing."""
    if not OCR_AVAILABLE:
        return None
    
    # Convert to RGB format for consistency
    if qimage.format() != QImage.Format.Format_RGB32:
        qimage = qimage.convertToFormat(QImage.Format.Format_RGB32)
    
    # Save QImage to buffer as PNG
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    qimage.save(buffer, "PNG")
    buffer.close()
    
    # Load as PIL Image
    pil_image = Image.open(io.BytesIO(buffer.data().data()))
    return pil_image


def clean_ocr_text(text: str) -> str:
    """
    Clean up OCR output text.
    
    - Removes common icon/symbol misreadings
    - Fixes multiple spaces
    - Removes empty lines
    - Preserves meaningful text
    """
    import re
    
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        cleaned = line
        
        # Remove leading symbols that are typically icon misreads
        # Pattern: symbols/letters at start followed by space, then actual text
        cleaned = re.sub(r'^[\>\<©®™¥€£¢¤§¶†‡•◊○●□■◆◇▲△▼▽►◄@%&]\s*', '', cleaned)
        
        # Remove 1-2 letter prefixes followed by space (folder icon misreads: C, Ce, Ca, Cf, CB, CF)
        cleaned = re.sub(r'^[A-Z][a-zA-Z]?\s+', '', cleaned)
        
        # Remove standalone symbols that aren't part of filenames
        cleaned = re.sub(r'[¥©®™°€£¢¤§¶†‡•◊○●□■◆◇▲△▼▽►◄←→↑↓↔↕❖❯❮❱❰⌘⌥⌃⎋⏎⇧⇪⇥]', '', cleaned)
        
        # Fix multiple spaces -> single space
        cleaned = re.sub(r'\s{2,}', ' ', cleaned)
        
        # Strip whitespace
        cleaned = cleaned.strip()
        
        # Remove lines that are just a single character (usually icon artifacts)
        if len(cleaned) <= 1 and not cleaned.isalnum():
            continue
        
        # Keep non-empty lines
        if cleaned:
            cleaned_lines.append(cleaned)
    
    return '\n'.join(cleaned_lines)


def preprocess_for_ocr(pil_image: "Image.Image") -> "Image.Image":
    """
    Preprocess image for better OCR accuracy.
    
    - Converts to grayscale
    - Detects dark theme and inverts if needed
    - Increases contrast
    """
    from PIL import ImageOps, ImageFilter
    import numpy as np
    
    # Convert to grayscale
    gray = pil_image.convert('L')
    
    # Check if image is dark-themed (average brightness < 128)
    img_array = np.array(gray)
    avg_brightness = np.mean(img_array)
    
    logger.debug(f"Image average brightness: {avg_brightness:.1f}")
    
    if avg_brightness < 128:
        # Dark theme - invert colors (light text on dark bg -> dark text on light bg)
        gray = ImageOps.invert(gray)
        logger.info("OCR: Detected dark theme, inverting image")
    
    # Increase contrast
    gray = ImageOps.autocontrast(gray, cutoff=2)
    
    # Optional: slight sharpening for small text
    gray = gray.filter(ImageFilter.SHARPEN)
    
    return gray


def extract_text(qimage: QImage, lang: str = "eng") -> str:
    """
    Extract text from a QImage using Tesseract OCR.
    
    Args:
        qimage: The QImage to extract text from
        lang: Language code for OCR (default: 'eng' for English)
    
    Returns:
        Extracted text as string, or empty string if OCR fails
    """
    if not OCR_AVAILABLE:
        logger.warning("OCR is not available - pytesseract not installed")
        return ""
    
    try:
        pil_image = qimage_to_pil(qimage)
        if pil_image is None:
            return ""
        
        # Preprocess image for better OCR
        processed = preprocess_for_ocr(pil_image)
        
        # Run OCR with optimized config for screen text
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(processed, lang=lang, config=custom_config)
        
        # Clean up OCR output
        text = clean_ocr_text(text)
        
        logger.info(f"OCR extracted {len(text)} characters")
        return text
    
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return ""


def extract_text_from_region(qimage: QImage, rect: QRect, lang: str = "eng") -> str:
    """
    Extract text from a specific region of a QImage.
    
    Args:
        qimage: The source QImage
        rect: The region to extract text from
        lang: Language code for OCR
    
    Returns:
        Extracted text as string
    """
    if rect.isNull() or rect.isEmpty():
        return ""
    
    # Crop the image to the specified region
    cropped = qimage.copy(rect)
    return extract_text(cropped, lang)
