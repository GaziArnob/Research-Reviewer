import numpy as np
import pymupdf

_engine = None


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def ocr_pages(pdf_bytes: bytes, max_pages: int = 40, dpi: int = 150) -> list[str]:
    """OCR every page image (RapidOCR, pure pip, runs offline). Text is ordered top-to-bottom, left-to-right."""
    engine = _get_engine()
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in list(doc)[:max_pages]:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        result, _ = engine(img)
        boxes = sorted(result or [], key=lambda r: (round(r[0][0][1] / 12), r[0][0][0]))  # row bands of ~12px
        pages.append("\n".join(r[1] for r in boxes))
    return pages
