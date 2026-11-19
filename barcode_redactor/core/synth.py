"""Synthetic test-document generator.

Produces laboratory-report-like pages with Code 128 barcodes (and optional QR
codes) plus "print-and-scan" degradations: rotation, blur, noise, JPEG
artefacts, low contrast, shadows and black-and-white thresholding.

Used by the unit tests, the evaluation script and the built-in ``--self-test``
of the packaged executable (so the .exe can verify that its bundled native
libraries work without needing any sample files).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Code 128 element widths (bar, space, bar, space, bar, space[, bar]) for values 0..106.
_C128 = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 "
    "112232 122132 122231 113222 123122 123221 223211 221132 221231 213212 223112 312131 "
    "311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 "
    "112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 113321 133121 "
    "313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 "
    "122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 "
    "124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 "
    "114311 411113 411311 113141 114131 311141 411131 211412 211214 211232 2331112"
).split()
_START_B = 104
_STOP = 106


def code128_widths(text: str) -> list[int]:
    """Return the module widths (alternating bar/space, starting with a bar) for Code 128-B."""
    values = [_START_B]
    for ch in text:
        code = ord(ch) - 32
        if not 0 <= code <= 94:
            raise ValueError(f"Character {ch!r} not encodable in Code 128-B")
        values.append(code)
    checksum = values[0] + sum(i * v for i, v in enumerate(values[1:], start=1))
    values.append(checksum % 103)
    values.append(_STOP)
    return [int(d) for v in values for d in _C128[v]]


def render_code128(text: str, module_px: float, height_px: int) -> np.ndarray:
    """Render a Code 128 symbol (no quiet zone) as a uint8 image, 0 = black bar."""
    widths = code128_widths(text)
    total_modules = sum(widths)
    width_px = int(round(total_modules * module_px))
    out = np.full((height_px, width_px), 255, np.uint8)
    pos = 0.0
    for i, w in enumerate(widths):
        x0, x1 = int(round(pos)), int(round(pos + w * module_px))
        if i % 2 == 0:
            out[:, x0:x1] = 0
        pos += w * module_px
    return out


def render_qr(text: str, module_px: int) -> np.ndarray:
    enc = cv2.QRCodeEncoder.create()
    qr = enc.encode(text)  # 1 px per module, includes a quiet zone
    qr = cv2.resize(qr, None, fx=module_px, fy=module_px, interpolation=cv2.INTER_NEAREST)
    return qr


@dataclass
class SyntheticPage:
    image: np.ndarray  # uint8 grayscale
    dpi: int
    # Ground-truth barcode outlines (4x2 float arrays, pixel coords) and their kinds.
    truth: list[np.ndarray] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)


_WORDS = (
    "Haemoglobin Glucose Fasting Serum Creatinine Urea Sodium Potassium Chloride Bilirubin "
    "Total Direct Platelet Count Neutrophils Lymphocytes Eosinophils Monocytes Basophils "
    "Cholesterol Triglycerides HDL LDL VLDL Albumin Globulin Calcium Phosphorus Uric Acid "
    "TSH Free T3 T4 Vitamin D B12 Ferritin Iron HbA1c ESR CRP SGOT SGPT Alkaline Phosphatase"
).split()
_UNITS = ["g/dL", "mg/dL", "mmol/L", "U/L", "%", "cells/cumm", "ng/mL", "pg/mL", "mIU/L", "mm/hr"]
_NAMES = ["Asha Patel", "Rohan Mehta", "Priya Sharma", "Vikram Rao", "Neha Joshi", "Arjun Desai"]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def make_lab_report(
    dpi: int = 300,
    seed: int = 0,
    barcodes: list[dict] | None = None,
    qr: dict | None = None,
    with_barcode: bool = True,
    page_mm: tuple[float, float] = (210.0, 297.0),
) -> SyntheticPage:
    """Generate a lab-report-like page.

    ``barcodes``: list of dicts with optional keys ``text``, ``x_mm``, ``y_mm``,
    ``module_mm``, ``height_mm``, ``hri`` (print digits below), ``vertical``.
    By default one barcode is placed near the top-right corner just above the
    patient-details block, like a typical laboratory report.
    """
    rng = random.Random(seed)
    mm = dpi / 25.4
    W, H = int(round(page_mm[0] * mm)), int(round(page_mm[1] * mm))
    pil = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(pil)
    f_title = _font(int(5.5 * mm))
    f_body = _font(int(3.0 * mm))
    f_small = _font(int(2.5 * mm))

    def text(x_mm, y_mm, s, font=f_body):
        d.text((x_mm * mm, y_mm * mm), s, fill=0, font=font)

    def hline(y_mm, x0=12, x1=page_mm[0] - 12, t=0.3):
        d.rectangle([x0 * mm, y_mm * mm, x1 * mm, (y_mm + t) * mm], fill=0)

    # Header
    text(14, 10, "CITY DIAGNOSTIC LABORATORY", f_title)
    text(14, 19, "12 Main Road, Ahmedabad  |  Tel 079-1234567  |  NABL accredited", f_small)
    hline(25)
    # Patient details block (left) – barcode sits top-right above/beside it.
    name = rng.choice(_NAMES)
    rows = [
        f"Patient Name : {name}",
        f"Age / Sex    : {rng.randint(18, 80)} Y / {rng.choice('MF')}",
        f"Referred By  : Dr. {rng.choice(_NAMES).split()[1]}",
        f"Sample ID    : {rng.randint(10**7, 10**8 - 1)}",
        f"Collected    : {rng.randint(1, 28):02d}/11/2026 {rng.randint(7, 11):02d}:{rng.randint(0, 59):02d}",
    ]
    for i, r in enumerate(rows):
        text(14, 42 + i * 5.5, r)
    text(110, 52, f"Reported : {rng.randint(1, 28):02d}/11/2026", f_body)
    text(110, 57.5, f"Lab No   : {rng.randint(1000, 9999)}", f_body)
    hline(72)
    # Results table
    text(14, 75, "TEST", f_body); text(90, 75, "RESULT", f_body)
    text(120, 75, "UNIT", f_body); text(150, 75, "REFERENCE RANGE", f_body)
    hline(81, t=0.2)
    y = 84.0
    n_rows = rng.randint(18, 28)
    for _ in range(n_rows):
        name_t = " ".join(rng.sample(_WORDS, rng.randint(1, 3)))
        text(14, y, name_t)
        text(90, y, f"{rng.uniform(0.1, 300):.1f}")
        text(120, y, rng.choice(_UNITS))
        lo = rng.uniform(0, 100)
        text(150, y, f"{lo:.1f} - {lo + rng.uniform(5, 100):.1f}")
        y += 6.2
        if y > page_mm[1] - 40:
            break
    # Table grid lines (vertical rules) – a classic false-positive trap.
    for x in (12, 88, 118, 148, page_mm[0] - 12):
        d.rectangle([x * mm, 73 * mm, (x + 0.25) * mm, y * mm], fill=0)
    hline(y + 1)
    text(14, page_mm[1] - 30, "*** End of Report ***", f_body)
    text(130, page_mm[1] - 22, "Dr. Pathologist, MD", f_body)
    text(14, page_mm[1] - 12, "This report is computer generated. Page 1 of 1", f_small)

    img = np.array(pil)
    truth, kinds = [], []
    if with_barcode:
        if barcodes is None:
            barcodes = [{}]
        for spec in barcodes:
            t = spec.get("text", f"S{rng.randint(10**8, 10**9 - 1)}")
            module_mm = spec.get("module_mm", 0.30)
            height_mm = spec.get("height_mm", 9.0)
            bc = render_code128(t, module_mm * mm, int(round(height_mm * mm)))
            if spec.get("hri", True):
                hri_h = int(3.6 * mm)
                strip = Image.new("L", (bc.shape[1], hri_h), 255)
                sd = ImageDraw.Draw(strip)
                fnt = _font(int(2.8 * mm))
                tw = sd.textlength(t, font=fnt)
                sd.text(((bc.shape[1] - tw) / 2, 0.3 * mm), t, fill=0, font=fnt)
                block = np.vstack([bc, np.array(strip)])
            else:
                block = bc
            bh, bw = bc.shape
            if spec.get("vertical"):
                block = np.rot90(block).copy()
            x_mm = spec.get("x_mm", page_mm[0] - 14 - bw / mm)
            y_mm = spec.get("y_mm", 28.5)
            x0, y0 = int(round(x_mm * mm)), int(round(y_mm * mm))
            hh, ww = block.shape
            # clear the area first (labels are printed on white)
            img[y0:y0 + hh, x0:x0 + ww] = np.minimum(255, block)
            if spec.get("vertical"):
                # after rot90 (counter-clockwise) the bars occupy the left-most ``bh`` columns
                box = np.array([[x0, y0], [x0 + bh, y0], [x0 + bh, y0 + bw], [x0, y0 + bw]], float)
            else:
                box = np.array([[x0, y0], [x0 + bw, y0], [x0 + bw, y0 + bh], [x0, y0 + bh]], float)
            truth.append(box)
            kinds.append("1d")
    if qr:
        q = render_qr(qr.get("text", "LAB-REPORT-0001"), max(1, int(round(qr.get("module_mm", 0.5) * mm))))
        x0, y0 = int(qr.get("x_mm", 170) * mm), int(qr.get("y_mm", page_mm[1] - 45) * mm)
        img[y0:y0 + q.shape[0], x0:x0 + q.shape[1]] = q
        ys, xs = np.nonzero(q < 128)  # ground truth = the symbol itself, without its quiet zone
        qx0, qy0, qx1, qy1 = x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1
        truth.append(np.array([[qx0, qy0], [qx1, qy0], [qx1, qy1], [qx0, qy1]], float))
        kinds.append("2d")
    return SyntheticPage(img, dpi, truth, kinds)


def degrade(
    page: SyntheticPage,
    seed: int = 0,
    rotate_deg: float = 0.0,
    blur_sigma: float = 0.0,
    noise_std: float = 0.0,
    jpeg_quality: int | None = None,
    contrast: float = 1.0,
    bw: bool = False,
    shadow: bool = False,
    scale_to_dpi: int | None = None,
) -> SyntheticPage:
    """Apply print-and-scan style degradations, transforming the ground truth accordingly."""
    rng = np.random.default_rng(seed)
    img = page.image.astype(np.float32)
    truth = [t.copy() for t in page.truth]
    dpi = page.dpi
    if scale_to_dpi and scale_to_dpi != dpi:
        f = scale_to_dpi / dpi
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
        truth = [t * f for t in truth]
        dpi = scale_to_dpi
    h, w = img.shape
    if rotate_deg:
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, 1.0)
        img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
        truth = [np.hstack([t, np.ones((4, 1))]) @ M.T for t in truth]
    if blur_sigma:
        img = cv2.GaussianBlur(img, (0, 0), blur_sigma)
    if contrast != 1.0:
        # compress toward light grey: faded toner
        img = 255 - (255 - img) * contrast
    if shadow:
        xs = np.linspace(0, 1, w, dtype=np.float32)
        grad = 1.0 - 0.35 * np.clip(1 - xs * 4, 0, 1)  # dark band at the left edge (book-fold shadow)
        img = img * grad[None, :]
    if noise_std:
        img = img + rng.normal(0, noise_std, img.shape).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if bw:
        img = np.where(img > 140, 255, 0).astype(np.uint8)
        # salt & pepper speckle typical of B&W scanners
        sp = rng.random(img.shape)
        img[sp < 0.0008] = 0
        img[sp > 0.9995] = 255
    if jpeg_quality:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    return SyntheticPage(img, dpi, truth, list(page.kinds))


def score_detection(page: SyntheticPage, polygons: list[np.ndarray]) -> dict:
    """Coverage of each ground-truth code and number of false-positive regions."""
    h, w = page.image.shape[:2]
    f = min(1.0, 1000.0 / max(h, w))
    det = np.zeros((int(h * f) + 1, int(w * f) + 1), np.uint8)
    for p in polygons:
        cv2.fillPoly(det, [np.round(np.asarray(p) * f).astype(np.int32)], 1)
    coverage = []
    for t in page.truth:
        m = np.zeros_like(det)
        cv2.fillPoly(m, [np.round(t * f).astype(np.int32)], 1)
        a = m.sum()
        coverage.append(float((m & det).sum()) / a if a else 0.0)
    gt = np.zeros_like(det)
    for t in page.truth:
        cv2.fillPoly(gt, [np.round(t * f).astype(np.int32)], 1)
    fps = 0
    for p in polygons:
        m = np.zeros_like(det)
        cv2.fillPoly(m, [np.round(np.asarray(p) * f).astype(np.int32)], 1)
        a = m.sum()
        if a and (m & gt).sum() / a < 0.3:
            fps += 1
    return {"coverage": coverage, "false_positives": fps}


def make_hard_negative(dpi: int = 300, seed: int = 0) -> SyntheticPage:
    """A page with no barcode but lots of barcode-like distractors (false-positive test)."""
    rng = random.Random(seed)
    mm = dpi / 25.4
    W, H = int(210 * mm), int(297 * mm)
    pil = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(pil)
    y = 15.0
    for size in (2.0, 2.4, 3.0):  # dense paragraphs in small fonts
        f = _font(int(size * mm))
        for _ in range(9):
            line = " ".join(rng.choice(_WORDS) for _ in range(14))
            d.text((14 * mm, y * mm), line, fill=0, font=f)
            y += size * 1.5
        y += 4
    # dense grid table with vertical rules every 6 mm
    top = y
    for i in range(12):
        d.rectangle([14 * mm, (top + i * 5) * mm, 196 * mm, (top + i * 5 + 0.25) * mm], fill=0)
    for j in range(31):
        x = 14 + j * 6
        d.rectangle([x * mm, top * mm, (x + 0.25) * mm, (top + 55) * mm], fill=0)
    y = top + 62
    # number columns / IDs in bold-ish large font
    f = _font(int(4 * mm))
    for i in range(5):
        d.text((14 * mm, (y + i * 7) * mm), "ID 1111 1111 IIII llll 1001 1101 0110", fill=0, font=f)
    y += 40
    # dashed and dotted lines, a hatched logo box, a signature-like scribble
    for k in range(4):
        for x in range(14, 190, 3):
            d.rectangle([x * mm, (y + k * 4) * mm, (x + 1.5) * mm, (y + k * 4 + 0.3) * mm], fill=0)
    for i in range(0, 120, 3):  # diagonal hatching inside a 30x30 mm box
        d.line([(150 * mm + i * mm / 4, (y + 20) * mm), (150 * mm, (y + 20) * mm + i * mm / 4)], fill=0,
               width=max(1, int(0.3 * mm)))
    pts = [(30 * mm + i * 2 * mm, (y + 30 + 3 * np.sin(i / 2)) * mm) for i in range(40)]
    d.line(pts, fill=0, width=max(1, int(0.4 * mm)))
    return SyntheticPage(np.array(pil), dpi, [], [])


def random_barcode_specs(seed: int, n: int = 2, with_default: bool = True) -> list[dict]:
    """``n`` barcodes at random, non-overlapping positions (+ the default top-right one)."""
    rng = random.Random(seed)
    boxes = [(150.0, 26.0, 197.0, 43.0)] if with_default else []
    specs: list[dict] = []
    tries = 0
    while len(specs) < n and tries < 500:
        tries += 1
        m = rng.choice([0.19, 0.25, 0.33])
        h = rng.uniform(5, 12)
        w = 156 * m  # 10-character Code 128 symbol
        x, y = rng.uniform(15, 196 - w), rng.uniform(90, 250)
        box = (x - 4, y - 4, x + w + 4, y + h + 3.6 + 4)
        if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in boxes):
            continue
        boxes.append(box)
        specs.append({"x_mm": x, "y_mm": y, "module_mm": m, "height_mm": h})
    return specs + ([{}] if with_default else [])
