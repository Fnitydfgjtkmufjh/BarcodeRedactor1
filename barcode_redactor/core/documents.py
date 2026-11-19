"""Page-by-page access to PDF and image documents (never loads a whole document)."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .detector import estimate_dpi

Image.MAX_IMAGE_PIXELS = 400_000_000  # large 600 dpi scans are legitimate input

PDF_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
SUPPORTED_EXT = PDF_EXT | IMAGE_EXT

# PDFium is not thread-safe: every call into it goes through this lock.
PDFIUM_LOCK = threading.RLock()


@dataclass
class RenderedPage:
    image: np.ndarray  # uint8, HxW (gray) or HxWx3 (BGR)
    dpi: float
    bilevel: bool  # page is pure black & white (scanned in B&W)
    page_size_pt: tuple[float, float] | None = None  # displayed PDF page size (rotation applied)


class DocumentError(Exception):
    pass


class Document:
    path: Path
    page_count: int

    def native_dpi(self, index: int) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def render(self, index: int, dpi: float | None = None) -> RenderedPage:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_document(path: str | Path) -> Document:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        return PdfDocument(path)
    if ext in IMAGE_EXT:
        return ImageDocument(path)
    raise DocumentError(f"Unsupported file type: {path.suffix}")


def _finish(arr: np.ndarray, bilevel_hint: bool | None) -> tuple[np.ndarray, bool]:
    """Reduce RGB to gray when the page is colourless and decide whether it is bilevel."""
    if arr.ndim == 3:
        sub = arr[::4, ::4].astype(np.int16)
        if np.abs(sub[..., 0] - sub[..., 1]).max() <= 12 and np.abs(sub[..., 1] - sub[..., 2]).max() <= 12:
            arr = np.ascontiguousarray(arr[..., 1])
        else:
            arr = np.ascontiguousarray(arr[..., ::-1])  # RGB -> BGR for OpenCV
    if bilevel_hint is not None:
        return arr, bilevel_hint
    if arr.ndim == 2:
        sub = arr[::3, ::3]
        mid = np.count_nonzero((sub > 40) & (sub < 215))
        return arr, mid < 0.004 * sub.size
    return arr, False


class PdfDocument(Document):
    def __init__(self, path: Path):
        import pypdfium2 as pdfium

        self.path = path
        with PDFIUM_LOCK:
            try:
                self._pdf = pdfium.PdfDocument(str(path))
                self.page_count = len(self._pdf)
            except Exception as e:  # corrupt / encrypted
                raise DocumentError(f"Cannot open PDF: {e}") from e
        self._info: dict[int, tuple[float, bool | None]] = {}

    def _page_info(self, index: int) -> tuple[float, bool | None]:
        """(native dpi, bilevel?) from the largest image on the page – i.e. the scan."""
        if index in self._info:
            return self._info[index]
        import pypdfium2.raw as pdfium_c

        dpi, bilevel = 300.0, None
        with PDFIUM_LOCK:
            page = self._pdf[index]
            try:
                best = 0.0
                for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=2):
                    try:
                        px = obj.get_px_size() if hasattr(obj, "get_px_size") else obj.get_size()
                        b = obj.get_bounds() if hasattr(obj, "get_bounds") else obj.get_pos()
                        w_pt = abs(b[2] - b[0])
                        if w_pt <= 0:
                            continue
                        area = abs((b[2] - b[0]) * (b[3] - b[1]))
                        if area > best:
                            best = area
                            h_pt = abs(b[3] - b[1])
                            # rotation-agnostic: compare the larger pixel side to the larger point side
                            dpi = max(px) / (max(w_pt, h_pt) / 72.0)
                            try:
                                bilevel = obj.get_metadata().bits_per_pixel == 1
                            except Exception:
                                bilevel = None
                    except Exception:
                        continue
                if best == 0.0:
                    dpi = 300.0  # vector / text PDF
            finally:
                page.close()
        dpi = float(min(600.0, max(150.0, round(dpi / 25.0) * 25.0)))
        self._info[index] = (dpi, bilevel)
        return self._info[index]

    def native_dpi(self, index: int) -> float:
        return self._page_info(index)[0]

    def render(self, index: int, dpi: float | None = None) -> RenderedPage:
        native, bilevel = self._page_info(index)
        dpi = float(dpi or native)
        with PDFIUM_LOCK:
            page = self._pdf[index]
            try:
                size_pt = tuple(float(v) for v in page.get_size())
                bitmap = page.render(scale=dpi / 72.0)
                pil = bitmap.to_pil()
            finally:
                page.close()
        arr = np.asarray(pil.convert("RGB"))
        arr, bl = _finish(arr, bilevel if dpi == native else None)
        return RenderedPage(arr, dpi, bl, size_pt)

    def close(self) -> None:
        with PDFIUM_LOCK:
            try:
                self._pdf.close()
            except Exception:
                pass


class ImageDocument(Document):
    def __init__(self, path: Path):
        self.path = path
        try:
            self._img = Image.open(path)
            self.page_count = getattr(self._img, "n_frames", 1) or 1
        except Exception as e:
            raise DocumentError(f"Cannot open image: {e}") from e
        self.mode = self._img.mode

    def _frame(self, index: int) -> Image.Image:
        self._img.seek(index)
        im = self._img
        if index == 0:
            im = ImageOps.exif_transpose(im)  # honour camera/scanner orientation tag
        return im

    def native_dpi(self, index: int) -> float:
        self._img.seek(index)
        d = self._img.info.get("dpi")
        try:
            if d and float(d[0]) >= 50:
                return float(d[0])
        except Exception:
            pass
        return estimate_dpi(*self._img.size)

    def render(self, index: int, dpi: float | None = None) -> RenderedPage:
        native = self.native_dpi(index)
        try:
            im = self._frame(index)
            im.load()
        except Exception as e:
            raise DocumentError(f"Cannot read page {index + 1}: {e}") from e
        bilevel = im.mode == "1"
        if im.mode in ("1", "L", "I;16", "I", "F"):
            im = im.convert("L")
            arr = np.asarray(im)
        else:
            if im.mode in ("RGBA", "LA", "P", "PA"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            arr = np.asarray(im.convert("RGB"))
        arr, bl = _finish(arr, True if bilevel else None)
        out_dpi = native
        if dpi and abs(dpi - native) > 1:
            import cv2

            f = dpi / native
            arr = cv2.resize(arr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
            out_dpi = dpi
        return RenderedPage(arr, out_dpi, bl)

    def close(self) -> None:
        try:
            self._img.close()
        except Exception:
            pass


__all__ = ["open_document", "Document", "DocumentError", "RenderedPage", "SUPPORTED_EXT", "PDFIUM_LOCK"]
