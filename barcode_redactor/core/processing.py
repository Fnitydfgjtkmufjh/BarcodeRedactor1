"""Detection, permanent redaction, output writing and CSV reporting (UI-independent)."""
from __future__ import annotations

import csv
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np
from PIL import Image, TiffImagePlugin

from .detector import BarcodeDetector, DetectorConfig
from .documents import Document, DocumentError, RenderedPage, open_document
from .models import DocumentEntry, PageResult, Region

log = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


@dataclass
class Options:
    redaction_color: str = "white"  # white | black
    sensitivity: float = 0.5
    padding_mm: float = 1.5
    redact_2d: bool = True
    include_hri_text: bool = True  # also cover the printed digits under the bars (usually the sample ID)
    output_dpi: int = 0  # PDFs only; 0 = match the scan resolution
    pdf_compression: str = "auto"  # auto (B&W->CCITT G4, gray/colour->JPEG) | lossless
    jpeg_quality: int = 90
    low_confidence: float = 0.7
    max_detect_dpi: int = 300


# --------------------------------------------------------------------------- redaction
def pad_px(options: Options, dpi: float) -> float:
    return max(0.0, options.padding_mm) / 25.4 * dpi


def redact_array(img: np.ndarray, regions: Iterable[Region], color: str, padding_px: float) -> np.ndarray:
    """Return a copy of ``img`` with every region's pixels overwritten by a solid colour."""
    out = img.copy()
    H, W = out.shape[:2]
    val = 255 if color == "white" else 0
    fill = val if out.ndim == 2 else (val,) * out.shape[2]
    for r in regions:
        p = r.polygon_px(W, H).astype(np.float32)
        (cx, cy), (w, h), ang = cv2.minAreaRect(p)
        box = cv2.boxPoints(((cx, cy), (w + 2 * padding_px + 1, h + 2 * padding_px + 1), ang))
        cv2.fillPoly(out, [np.round(box).astype(np.int32)], fill, lineType=cv2.LINE_8)
    return out


# --------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, options: Options, is_cancelled: Callable[[], bool] = lambda: False):
        self.options = options
        self.is_cancelled = is_cancelled
        self.detector = BarcodeDetector(DetectorConfig(
            sensitivity=options.sensitivity,
            detect_2d=options.redact_2d,
            include_hri_text=options.include_hri_text,
        ))

    def _check(self):
        if self.is_cancelled():
            raise Cancelled()

    # ---- detection ----------------------------------------------------------------
    def detect_rendered(self, page: PageResult, rp: RenderedPage) -> PageResult:
        if page.edited:  # never overwrite the user's corrections
            return page
        res = self.detector.detect(rp.image, rp.dpi)
        H, W = rp.image.shape[:2]
        regions = [Region.from_polygon(c.polygon, W, H, method=c.method_label, kind=c.kind,
                                       confidence=c.confidence, fmt=c.fmt)
                   for c in res.candidates if c.kind == "1d" or self.options.redact_2d]
        suspects = [Region.from_polygon(s, W, H, method="suggested", confidence=0.0) for s in res.suspects]
        reasons = []
        if not regions:
            reasons.append("No barcode found - check page manually")
        if any(r.confidence < self.options.low_confidence for r in regions):
            reasons.append("Low-confidence detection")
        if suspects:
            reasons.append(f"{len(suspects)} barcode-like area(s) not redacted")
        page.regions, page.suspects = regions, suspects
        page.review_reasons = reasons
        page.detected, page.reviewed, page.error = True, False, ""
        page.decoder_used = res.decoder_available
        return page

    def detect_entry(self, entry: DocumentEntry, force: bool = False,
                     on_page: Callable[[DocumentEntry, int], None] | None = None) -> None:
        try:
            doc = open_document(entry.path)
        except DocumentError as e:
            entry.error, entry.status = str(e), "Error"
            raise
        with doc:
            entry.page_count = doc.page_count
            for i in range(doc.page_count):
                self._check()
                page = entry.page(i)
                if (page.detected and not force) or page.edited:
                    if on_page:
                        on_page(entry, i)
                    continue
                try:
                    rp = doc.render(i, min(doc.native_dpi(i), self.options.max_detect_dpi))
                    self.detect_rendered(page, rp)
                except Cancelled:
                    raise
                except Exception as e:  # keep going with the next page
                    log.exception("page %s of %s failed", i + 1, entry.path)
                    page.error, page.detected = f"{type(e).__name__}: {e}", True
                    page.regions, page.review_reasons = [], ["Processing error"]
                if on_page:
                    on_page(entry, i)
        entry.error = ""
        entry.status = "Review" if entry.pages_needing_review else "Detected"

    # ---- saving -------------------------------------------------------------------
    def output_path_for(self, entry: DocumentEntry, out_dir: Path) -> Path:
        out_dir = Path(out_dir)
        sub = Path()
        if entry.base_dir is not None:
            try:
                sub = entry.path.parent.relative_to(entry.base_dir)
            except ValueError:
                sub = Path()
        target_dir = out_dir / sub
        stem, ext = entry.path.stem, entry.path.suffix
        cand = target_dir / f"{stem}_redacted{ext}"
        n = 2
        while cand.exists() or cand.resolve() == entry.path.resolve():
            cand = target_dir / f"{stem}_redacted_{n}{ext}"
            n += 1
        return cand

    def save_entry(self, entry: DocumentEntry, out_dir: Path,
                   on_page: Callable[[DocumentEntry, int], None] | None = None) -> Path:
        out = self.output_path_for(entry, out_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        try:
            doc = open_document(entry.path)
        except DocumentError as e:
            entry.error, entry.status = str(e), "Error"
            raise
        try:
            with doc:
                entry.page_count = doc.page_count
                if entry.path.suffix.lower() == ".pdf":
                    self._save_pdf(doc, entry, tmp, on_page)
                else:
                    self._save_image(doc, entry, tmp, on_page)
            os.replace(tmp, out)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        entry.output_path = out
        entry.status = "Saved (review)" if entry.pages_needing_review else "Saved"
        return out

    def _page_for_output(self, doc: Document, entry: DocumentEntry, i: int, dpi: float | None):
        rp = doc.render(i, dpi)
        page = entry.page(i)
        if not page.detected and not page.edited:
            self.detect_rendered(page, rp)
        rp.image = redact_array(rp.image, page.regions, self.options.redaction_color, pad_px(self.options, rp.dpi))
        return rp

    def _save_pdf(self, doc: Document, entry: DocumentEntry, tmp: Path, on_page) -> None:
        import img2pdf

        workdir = Path(tempfile.mkdtemp(prefix="bcr_"))
        try:
            files: list[str] = []
            sizes: list[tuple[float, float]] = []
            for i in range(doc.page_count):
                self._check()
                dpi = self.options.output_dpi or doc.native_dpi(i)
                rp = self._page_for_output(doc, entry, i, dpi)
                files.append(str(self._encode_pdf_page(rp.image, rp.dpi, rp.bilevel, workdir / f"p{i:05d}")))
                h, w = rp.image.shape[:2]
                sizes.append(rp.page_size_pt or (w * 72.0 / rp.dpi, h * 72.0 / rp.dpi))
                del rp
                if on_page:
                    on_page(entry, i)
            self._check()
            size_iter = iter(sizes)

            def layout(_w, _h, _dpi):  # keep each page exactly the original page size
                pw, ph = next(size_iter)
                return pw, ph, pw, ph

            # A brand-new PDF containing only the redacted page images: no text layer, no original
            # image objects, no annotations, no metadata and no dates from the source file survive.
            with open(tmp, "wb") as fh:
                img2pdf.convert(files, outputstream=fh, layout_fun=layout, nodate=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _encode_pdf_page(self, img: np.ndarray, dpi: float, bilevel: bool, base: Path) -> Path:
        d = int(round(dpi))
        lossless = self.options.pdf_compression == "lossless"
        if bilevel and img.ndim == 2:
            pil = Image.fromarray(img).point(lambda v: 255 if v >= 128 else 0).convert("1")
            path = base.with_suffix(".tif")
            pil.save(path, format="TIFF", compression="group4", dpi=(d, d))
            return path
        pil = Image.fromarray(img if img.ndim == 2 else img[..., ::-1])
        if lossless:
            path = base.with_suffix(".png")
            pil.save(path, format="PNG", dpi=(d, d), optimize=False)
        else:
            path = base.with_suffix(".jpg")
            pil.save(path, format="JPEG", quality=int(self.options.jpeg_quality), dpi=(d, d))
        return path

    def _save_image(self, doc: Document, entry: DocumentEntry, tmp: Path, on_page) -> None:
        ext = entry.path.suffix.lower()
        src_mode = getattr(doc, "mode", "L")

        def to_pil(img: np.ndarray, bilevel: bool) -> Image.Image:
            pil = Image.fromarray(img if img.ndim == 2 else img[..., ::-1])
            if src_mode == "1" or (bilevel and ext in (".tif", ".tiff", ".png")):
                pil = pil.point(lambda v: 255 if v >= 128 else 0).convert("1")
            return pil

        if ext in (".tif", ".tiff"):
            with _TiffWriter(str(tmp), True) as tf:  # streams pages to disk one at a time
                for i in range(doc.page_count):
                    self._check()
                    rp = self._page_for_output(doc, entry, i, None)
                    pil = to_pil(rp.image, rp.bilevel)
                    comp = "group4" if pil.mode == "1" else "tiff_adobe_deflate"
                    d = int(round(rp.dpi))
                    if i:
                        tf.newFrame()
                    pil.save(tf, format="TIFF", compression=comp, dpi=(d, d))
                    if on_page:
                        on_page(entry, i)
            return
        self._check()
        rp = self._page_for_output(doc, entry, 0, None)
        pil = to_pil(rp.image, rp.bilevel)
        d = int(round(rp.dpi))
        if ext in (".jpg", ".jpeg"):
            if pil.mode == "1":
                pil = pil.convert("L")
            pil.save(tmp, format="JPEG", quality=95, subsampling=0, dpi=(d, d))
        else:
            pil.save(tmp, format="PNG", dpi=(d, d))
        if on_page:
            on_page(entry, 0)


class _TiffWriter(TiffImagePlugin.AppendingTiffWriter):
    """Pillow's writer may be closed twice (context exit + garbage collection); make it idempotent."""

    _done = False

    def close(self):
        if self._done:
            return
        self._done = True
        super().close()


# --------------------------------------------------------------------------- report
REPORT_COLUMNS = ["File", "Page", "Barcode regions", "Detection method", "Review required",
                  "Reviewed by user", "Review reasons", "Error", "Output file"]


def write_report(entries: Iterable[DocumentEntry], csv_path: Path) -> Path:
    """CSV with one row per page. Decoded barcode *content* is never written."""
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(REPORT_COLUMNS)
        for e in entries:
            if e.error and not e.pages:
                w.writerow([str(e.path), "-", 0, "none", "Yes", "No", "File could not be processed", e.error, ""])
                continue
            for i in range(max(e.page_count, len(e.pages))):
                p = e.page(i)
                w.writerow([
                    str(e.path), i + 1, len(p.regions), p.method_label,
                    "Yes" if p.review_required else "No",
                    "Yes" if p.reviewed else "No",
                    "; ".join(p.review_reasons) if p.detected else "Not processed",
                    p.error or e.error,
                    str(e.output_path) if e.output_path else "",
                ])
    return csv_path


def collect_files(paths: Iterable[str | Path]) -> list[tuple[Path, Path | None]]:
    """Expand files/folders into (file, base_folder) pairs, sorted, de-duplicated."""
    from .documents import SUPPORTED_EXT

    seen: set[Path] = set()
    out: list[tuple[Path, Path | None]] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXT and "_redacted" not in f.stem:
                    r = f.resolve()
                    if r not in seen:
                        seen.add(r)
                        out.append((f, p))
        elif p.is_file() and p.suffix.lower() in SUPPORTED_EXT:
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append((p, None))
    return out


__all__ = ["Options", "Engine", "Cancelled", "redact_array", "write_report", "collect_files"]
