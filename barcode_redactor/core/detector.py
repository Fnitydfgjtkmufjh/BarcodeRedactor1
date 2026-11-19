"""Hybrid barcode detector.

Method A – decoding:  ZXing-C++ (if installed) reads 1D/2D symbols and reports
their position. OpenCV's QR detector adds 2D locations even when the payload
cannot be decoded.

Method B – visual:    a structure-tensor analysis finds regions whose edges are
strong *and* all share one orientation (parallel bars). Every proposal – from
either method – is then verified in a rotation-normalised patch: the bars must
be solid across their full height, dense, numerous and consistent from row to
row. This rejects text, table rules and other line art, while still accepting
printed-and-rescanned barcodes that no longer decode.

All geometry is returned as oriented quadrilaterals in the pixel coordinates of
the image passed in, so tilted barcodes are covered tightly.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger(__name__)

try:  # Method A (optional at import time so the engine still works without it)
    import zxingcpp  # type: ignore
except Exception:  # pragma: no cover - depends on environment
    zxingcpp = None


# --------------------------------------------------------------------------- data types
@dataclass
class DetectorConfig:
    sensitivity: float = 0.5  # 0 = strict (fewer false positives) … 1 = aggressive (fewer misses)
    detect_2d: bool = True  # QR / DataMatrix / PDF417 / Aztec
    use_decoder: bool = True
    include_hri_text: bool = False  # extend 1D regions over the printed digits below/above the bars


@dataclass
class Candidate:
    polygon: np.ndarray  # (4, 2) float32, pixel coordinates of the analysed image
    kind: str  # "1d" | "2d"
    methods: set[str] = field(default_factory=set)
    confidence: float = 1.0
    fmt: str = ""  # symbology if decoded (decoded *content* is never stored)

    @property
    def method_label(self) -> str:
        order = ["decoded", "visual"]
        ms = [m for m in order if m in self.methods] + sorted(self.methods - set(order))
        return "+".join(ms) if ms else "unknown"


@dataclass
class DetectionResult:
    candidates: list[Candidate]
    near_misses: int = 0
    suspects: list[np.ndarray] = field(default_factory=list)  # barcode-like regions NOT redacted
    decoder_available: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class _Analysis:
    polygon: np.ndarray  # in analysis-image coordinates
    ratio: float  # min over criteria of value/threshold (>=1 means accepted)
    n_bars: int
    height_mm: float
    span_mm: float


# --------------------------------------------------------------------------- helpers
def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _rect_poly(cx: float, cy: float, length: float, height: float, theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    u = np.array([c, s]) * length / 2
    v = np.array([-s, c]) * height / 2
    ctr = np.array([cx, cy])
    return np.array([ctr - u - v, ctr + u - v, ctr + u + v, ctr - u + v], np.float32)


def _poly_frame(poly: np.ndarray) -> tuple[float, float, float, float, float]:
    """(cx, cy, length, height, theta) of a quadrilateral whose first edge runs along the code."""
    p = np.asarray(poly, np.float64)
    cx, cy = p.mean(0)
    top = p[1] - p[0]
    bottom = p[2] - p[3]
    d = top + bottom
    theta = math.atan2(d[1], d[0])
    length = (np.linalg.norm(top) + np.linalg.norm(bottom)) / 2
    height = (np.linalg.norm(p[3] - p[0]) + np.linalg.norm(p[2] - p[1])) / 2
    return cx, cy, length, height, theta


def poly_area(poly: np.ndarray) -> float:
    return float(abs(cv2.contourArea(np.asarray(poly, np.float32))))


def overlap_ratio(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection area divided by the smaller polygon's area."""
    a32 = cv2.convexHull(np.asarray(a, np.float32))
    b32 = cv2.convexHull(np.asarray(b, np.float32))
    try:
        inter, _ = cv2.intersectConvexConvex(a32, b32)
    except cv2.error:
        return 0.0
    small = min(poly_area(a32), poly_area(b32))
    return float(inter) / small if small > 0 else 0.0


# --------------------------------------------------------------------------- detector
class BarcodeDetector:
    ANALYSIS_DPI = 200.0  # bars are analysed at ≤200 dpi: fast, and enough for 0.19 mm modules

    def __init__(self, config: DetectorConfig | None = None):
        self.config = config or DetectorConfig()

    # ------------------------------------------------------------------ public API
    @staticmethod
    def decoder_available() -> bool:
        return zxingcpp is not None

    def detect(self, image: np.ndarray, dpi: float | None = None) -> DetectionResult:
        cfg = self.config
        gray = to_gray(image)
        H, W = gray.shape
        if not dpi or dpi < 50:
            dpi = estimate_dpi(W, H)
        notes: list[str] = []

        # Analysis image at a normalised resolution.
        scale = min(1.0, self.ANALYSIS_DPI / dpi)
        ga = gray if scale == 1.0 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ppmm = dpi * scale / 25.4

        cands: list[Candidate] = []
        suspects: list[np.ndarray] = []

        # ---- Method A: decoding --------------------------------------------------------
        decoded = []
        if cfg.use_decoder and zxingcpp is not None:
            decoded = self._zxing(gray, dpi)
        elif cfg.use_decoder:
            notes.append("barcode decoder library not available - visual detection only")

        for poly, kind, fmt in decoded:
            if kind == "2d":
                if cfg.detect_2d:
                    cands.append(Candidate(poly.astype(np.float32), "2d", {"decoded"}, 1.0, fmt))
                continue
            # zxing reports the scan line(s) for linear codes: recover the full bar height.
            cx, cy, L, h, th = _poly_frame(poly * scale)
            if L / ppmm < 5.0:  # too short to be a real linear symbol: decoder noise
                continue
            res = self._analyze(ga, ppmm, cx, cy, max(L, 5), max(h, 1), th, sens=1.0,
                                search_mm=max(15.0, 0.6 * L / ppmm))
            if res is not None and res.ratio >= 1.0:
                p, conf = res.polygon / scale, 1.0
            else:
                # Decoded but the bars could not be confirmed in the image: still redact (safer),
                # but with low confidence so the page is sent to review.
                hh = max(h, 0.3 * L)
                p, conf = _rect_poly(cx, cy, L * 1.04, hh, th) / scale, 0.5
            cands.append(Candidate(p.astype(np.float32), "1d", {"decoded"}, conf, fmt))

        # ---- Method B: visual -----------------------------------------------------------
        proposals = self._proposals(ga, ppmm)
        proposals += self._proposals_cv(ga)
        if ga.shape[0] * ga.shape[1] > 0 and ppmm > 3.0:  # coarse scale for very large codes
            g2 = cv2.resize(ga, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            proposals += [(cx * 2, cy * 2, L * 2, h * 2, th) for (cx, cy, L, h, th) in self._proposals(g2, ppmm / 2)]

        s = cfg.sensitivity
        for (cx, cy, L, h, th) in proposals:
            res = self._analyze(ga, ppmm, cx, cy, L, h, th, sens=s, search_mm=max(4.0, h / ppmm))
            if res is None:
                continue
            if res.ratio >= 1.0:
                conf = min(1.0, 0.55 + 0.45 * min(1.0, (res.ratio - 1.0) / 0.5))
                cands.append(Candidate((res.polygon / scale).astype(np.float32), "1d", {"visual"}, conf))
            elif res.ratio >= 0.75 and res.n_bars >= 8 and res.height_mm >= 2.0:
                # Looks somewhat barcode-like but not convincingly: check it isn't already covered.
                near_poly = res.polygon / scale
                suspects.append(near_poly.astype(np.float32))

        if cfg.detect_2d:
            for poly in self._qr_cv(gray):
                cands.append(Candidate(poly.astype(np.float32), "2d", {"visual"}, 0.8, "QRCode"))

        merged = self._merge(cands)
        if cfg.include_hri_text:
            merged = [self._with_hri(ga, ppmm, scale, c) if c.kind == "1d" else c for c in merged]
        merged = [c for c in merged if poly_area(c.polygon) > 4]
        # A suspect that a final region already covers is not a miss; de-duplicate the rest.
        uniq: list[np.ndarray] = []
        for sp in suspects:
            if any(overlap_ratio(sp, c.polygon) > 0.3 for c in merged):
                continue
            if any(overlap_ratio(sp, u) > 0.5 for u in uniq):
                continue
            uniq.append(sp)
        return DetectionResult(merged, len(uniq), uniq, zxingcpp is not None, notes)

    # ------------------------------------------------------------------ Method A helpers
    def _zxing(self, gray: np.ndarray, dpi: float) -> list[tuple[np.ndarray, str, str]]:
        out: list[tuple[np.ndarray, str, str]] = []
        img = gray
        f = 1.0
        if dpi > 400:  # zxing is faster on ~300 dpi and loses nothing on 1D codes
            f = 300.0 / dpi
            img = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        variants = [img]
        # Faded/low-contrast scans: a contrast-stretched variant often decodes when the original does not.
        variants.append(cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(img))
        seen: list[np.ndarray] = []
        formats = _zxing_formats(self.config.detect_2d)
        for var in variants:
            try:
                try:
                    if formats is not None:
                        results = zxingcpp.read_barcodes(var, formats=formats, try_rotate=True,
                                                         try_downscale=True)
                    else:
                        results = zxingcpp.read_barcodes(var, try_rotate=True, try_downscale=True)
                except TypeError:  # older API
                    results = zxingcpp.read_barcodes(var)
            except Exception as e:  # pragma: no cover
                log.warning("zxing failed: %s", e)
                continue
            for r in results:
                if hasattr(r, "valid") and not r.valid:
                    continue
                p = r.position
                poly = np.array([[p.top_left.x, p.top_left.y], [p.top_right.x, p.top_right.y],
                                 [p.bottom_right.x, p.bottom_right.y], [p.bottom_left.x, p.bottom_left.y]],
                                np.float32) / f
                fmt = getattr(r.format, "name", None) or str(r.format).split(".")[-1]
                if fmt not in _ALLOWED_1D and fmt not in _ALLOWED_2D:
                    continue  # e.g. DataBar / DXFilmEdge: frequent false reads on noisy scans
                kind = "2d" if _is_2d_format(fmt) else "1d"
                if any(overlap_ratio(poly, q) > 0.5 for q in seen if poly_area(q) > 4 and poly_area(poly) > 4):
                    continue
                seen.append(poly)
                out.append((poly, kind, fmt))
        return out

    @staticmethod
    def _qr_cv(gray: np.ndarray) -> list[np.ndarray]:
        polys = []
        try:
            det = cv2.QRCodeDetector()
            ok, pts = det.detectMulti(gray)
            if ok and pts is not None:
                for p in pts:
                    p = np.asarray(p, np.float32).reshape(4, 2)
                    if poly_area(p) > 400:
                        polys.append(p)
        except cv2.error:
            pass
        return polys

    # ------------------------------------------------------------------ Method B helpers
    def _proposals(self, g: np.ndarray, ppmm: float) -> list[tuple[float, float, float, float, float]]:
        """Regions with strong, coherent (parallel) edges -> oriented rectangles."""
        f = g.astype(np.float32) / 255.0
        k = max(3, int(round(3.0 * ppmm)) | 1)
        kern = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        local_range = cv2.dilate(f, kern) - cv2.erode(f, kern)
        norm = 1.0 / (local_range + 0.12)  # contrast normalisation: faded codes look like crisp ones
        gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3) * norm
        gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3) * norm
        sigma = 1.0 * ppmm
        jxx = cv2.GaussianBlur(gx * gx, (0, 0), sigma)
        jyy = cv2.GaussianBlur(gy * gy, (0, 0), sigma)
        jxy = cv2.GaussianBlur(gx * gy, (0, 0), sigma)
        tr = jxx + jyy
        diff = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2)
        coh = diff / (tr + 1e-6)
        s = self.config.sensitivity
        props = []
        # Two mask levels: the loose one catches faded/damaged codes, the strict one keeps a code
        # separate from text/table lines it touches (which the loose mask may merge it with).
        for c_thr, d_thr in ((_lerp(0.75, 0.55, s), _lerp(2.0, 0.8, s)), (0.9, _lerp(3.0, 2.0, s))):
            mask = ((coh > c_thr) & (diff > d_thr)).astype(np.uint8)
            props += self._components(mask, ppmm, jxx - jyy, 2 * jxy)
        return props

    @staticmethod
    def _components(mask: np.ndarray, ppmm: float, vx_img: np.ndarray, vy_img: np.ndarray):
        kc = max(3, int(round(1.2 * ppmm)) | 1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kc, kc)))
        ko = max(3, int(round(0.8 * ppmm)) | 1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ko, ko)))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        min_area = (4.0 * ppmm) * (1.5 * ppmm)
        props = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < min_area:
                continue
            ys, xs = np.nonzero(labels[y:y + h, x:x + w] == i)
            xs = xs + x
            ys = ys + y
            vx = float(vx_img[ys, xs].sum())
            vy = float(vy_img[ys, xs].sum())
            theta = 0.5 * math.atan2(vy, vx)  # gradient direction = along the code (across the bars)
            c, sn = math.cos(theta), math.sin(theta)
            u = xs * c + ys * sn
            v = -xs * sn + ys * c
            u0, u1 = np.percentile(u, [0.5, 99.5])
            v0, v1 = np.percentile(v, [0.5, 99.5])
            uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
            cx = uc * c - vc * sn
            cy = uc * sn + vc * c
            props.append((cx, cy, float(u1 - u0), float(v1 - v0), theta))
        return props

    @staticmethod
    def _proposals_cv(g: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        """OpenCV's own 1D localiser as an extra proposal source (verified like the others)."""
        out = []
        try:
            det = cv2.barcode.BarcodeDetector()
            ok, pts = det.detect(g)
        except Exception:
            return out
        if not ok or pts is None:
            return out
        for p in np.asarray(pts, np.float32).reshape(-1, 4, 2):
            rect = cv2.minAreaRect(p)
            (cx, cy), (w, h), ang = rect
            th = math.radians(ang)
            if h > w:  # make "length" the long side
                w, h = h, w
                th += math.pi / 2
            out.append((cx, cy, w, h, th))
        return out

    def _analyze(self, g: np.ndarray, ppmm: float, cx: float, cy: float, L: float, Hh: float,
                 theta: float, sens: float, search_mm: float) -> _Analysis | None:
        """Verify/refine a proposal in a patch where the bars are vertical."""
        # theta is the direction across the bars; fold so that patch x runs along the code.
        mx = 3.0 * ppmm + 0.1 * L
        my = search_mm * ppmm
        pw = int(min(L + 2 * mx, 220 * ppmm))
        ph = int(min(Hh + 2 * my, 120 * ppmm))
        if pw < 10 or ph < 6:
            return None
        c, s = math.cos(theta), math.sin(theta)
        A = np.array([[c, -s, cx - c * pw / 2 + s * ph / 2],
                      [s, c, cy - s * pw / 2 - c * ph / 2]], np.float64)
        patch = cv2.warpAffine(g, A, (pw, ph), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        rc = ph / 2
        band_h = max(2, int(min(Hh, 6 * ppmm) * 0.3))
        core = patch[int(rc - band_h):int(rc + band_h) + 1, :]
        lo, hi = np.percentile(core, [3, 97])
        if hi - lo < 25:
            return None
        thr, _ = cv2.threshold(core, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ink = (patch < thr).astype(np.float32)
        x0c, x1c = int(max(0, mx - 2 * ppmm)), int(min(pw, pw - mx + 2 * ppmm))
        ref = ink[int(rc - band_h):int(rc + band_h) + 1, x0c:x1c].mean(0)
        if ref.std() < 0.15:
            return None
        # --- vertical extent: rows whose profile matches the reference profile
        sm = cv2.blur(ink[:, x0c:x1c], (1, 3))
        xc = sm - sm.mean(1, keepdims=True)
        rr = ref - ref.mean()
        corr = (xc @ rr) / (np.linalg.norm(xc, axis=1) * np.linalg.norm(rr) + 1e-6)
        row_thr = _lerp(0.6, 0.45, sens)
        r = int(rc)
        if corr[r] < row_thr:
            near_rows = np.nonzero(corr[int(rc - band_h):int(rc + band_h) + 1] >= row_thr)[0]
            if len(near_rows) == 0:
                return None
            r = int(rc - band_h) + int(near_rows[len(near_rows) // 2])
        tol = max(1, int(0.4 * ppmm))  # tolerate thin scratches / fold lines
        r0 = r
        miss = 0
        while r0 - 1 >= 0 and miss <= tol:
            r0 -= 1
            miss = miss + 1 if corr[r0] < row_thr else 0
        r0 += miss
        r1 = r
        miss = 0
        while r1 + 1 < ph and miss <= tol:
            r1 += 1
            miss = miss + 1 if corr[r1] < row_thr else 0
        r1 -= miss
        height_px = r1 - r0 + 1
        if height_px < 3:
            return None
        # --- horizontal structure: solid bars across that height
        col = ink[r0:r1 + 1, :].mean(0)
        bars = _runs(col > 0.3)  # low threshold: thin bars break up in B&W scans
        if len(bars) < 3:
            return None
        med_w = float(np.median([b1 - b0 for b0, b1 in bars]))
        max_gap = max(3.0 * ppmm, 8 * med_w, 3)
        clusters: list[list[tuple[int, int]]] = [[bars[0]]]
        for b in bars[1:]:
            if b[0] - clusters[-1][-1][1] <= max_gap:
                clusters[-1].append(b)
            else:
                clusters.append([b])
        best = max(clusters, key=len)
        n_bars = len(best)
        xs0, xs1 = best[0][0], best[-1][1]
        span_px = xs1 - xs0
        span_mm = span_px / ppmm
        height_mm = height_px / ppmm
        seg = col[xs0:xs1]
        solid = float((seg > 0.8).sum()) / max(1.0, float((seg > 0.2).sum()))
        widths = np.array([b1 - b0 for b0, b1 in best], np.float32)
        on = np.zeros_like(seg, bool)
        for b0, b1 in best:
            on[b0 - xs0:b1 - xs0] = True
        purity = float(seg[on].mean() - (seg[~on].mean() if (~on).any() else 0.0))
        density = n_bars / max(span_mm, 1e-3)
        max_bar_mm = float(widths.max()) / ppmm
        aspect = height_mm / max(span_mm, 1e-3)
        # 1D symbologies use several element widths; text strokes and hatching are uniform.
        width_cv = float(widths.std() / max(widths.mean(), 1e-3))

        crit = [
            (n_bars, _lerp(16, 8, sens)),
            (height_mm, _lerp(4.5, 2.5, sens)),
            (span_mm, _lerp(10, 5, sens)),
            (solid, _lerp(0.7, 0.4, sens)),
            (purity, _lerp(0.6, 0.35, sens)),
            (density, _lerp(0.7, 0.35, sens)),
            (aspect, _lerp(0.06, 0.03, sens)),
            (width_cv, _lerp(0.3, 0.15, sens)),
        ]
        ratio = min(v / t for v, t in crit)
        # very wide "bars" (relative to the code) are solid blocks, not barcodes
        ratio = min(ratio, max(3.0, 0.2 * span_mm) / max(max_bar_mm, 1e-3))
        if aspect > 4.0:  # taller than wide by 4x: stripes/hatching rather than a 1D code
            ratio = min(ratio, 0.5)
        # map the refined box back to analysis-image coordinates
        pts = np.array([[xs0, r0], [xs1, r0], [xs1, r1 + 1], [xs0, r1 + 1]], np.float64)
        poly = (np.hstack([pts, np.ones((4, 1))]) @ A.T).astype(np.float32)
        return _Analysis(poly, float(ratio), n_bars, height_mm, span_mm)

    def _with_hri(self, ga: np.ndarray, ppmm: float, scale: float, cand: Candidate) -> Candidate:
        """Extend a 1D region over a human-readable text line printed directly below/above it."""
        cx, cy, L, h, th = _poly_frame(cand.polygon * scale)
        # Normalise orientation so 'down' in the patch is the reading direction's down (bars upright).
        if math.cos(th) < 0:
            th += math.pi
        mx = 0.5 * ppmm
        my = 7.0 * ppmm
        pw, ph = int(L + 2 * mx), int(h + 2 * my)
        c, s = math.cos(th), math.sin(th)
        A = np.array([[c, -s, cx - c * pw / 2 + s * ph / 2], [s, c, cy - s * pw / 2 - c * ph / 2]])
        patch = cv2.warpAffine(ga, A, (pw, ph), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=255)
        top, bot = int(my), int(my + h)
        bars = patch[top:bot, int(mx):int(mx + L)]
        if bars.size == 0:
            return cand
        paper, dark = float(np.percentile(patch, 90)), float(np.percentile(bars, 5))
        if paper - dark < 20:
            return cand
        ink = patch < (paper + dark) / 2  # adaptive: works for faded / grey printing too
        rows = ink[:, int(mx):int(mx + L)].mean(1)

        def scan(start: int, step: int) -> int:
            gap_max, band_max = int(2.5 * ppmm), int(5.0 * ppmm)
            r, n = start, 0
            while 0 <= r + step < ph and n < gap_max and rows[r + step] < 0.01:
                r += step
                n += 1
            if n >= gap_max or not (0 <= r + step < ph):
                return start
            m = 0
            while 0 <= r + step < ph and m < band_max and rows[r + step] >= 0.005:
                r += step
                m += 1
            if m >= band_max or m < int(1.0 * ppmm):
                return start
            return r

        new_bot = scan(bot - 1, +1)
        new_top = scan(top, -1)
        if new_bot == bot - 1 and new_top == top:
            return cand
        pts = np.array([[mx, new_top], [mx + L, new_top], [mx + L, new_bot + 1], [mx, new_bot + 1]], float)
        poly = (np.hstack([pts, np.ones((4, 1))]) @ A.T) / scale
        return Candidate(poly.astype(np.float32), cand.kind, cand.methods | {"hri"}, cand.confidence, cand.fmt)

    # ------------------------------------------------------------------ merging
    @staticmethod
    def _merge(cands: list[Candidate]) -> list[Candidate]:
        items = sorted(cands, key=lambda c: (-len(c.methods), -c.confidence))
        out: list[Candidate] = []
        for c in items:
            for o in out:
                if o.kind == c.kind and overlap_ratio(o.polygon, c.polygon) > 0.35:
                    pts = np.vstack([o.polygon, c.polygon])
                    if o.kind == "1d":
                        rect = cv2.minAreaRect(pts)
                        o.polygon = _order_poly(cv2.boxPoints(rect), _poly_frame(o.polygon)[4])
                    else:
                        o.polygon = cv2.boxPoints(cv2.minAreaRect(pts)).astype(np.float32)
                    o.methods |= c.methods
                    o.confidence = max(o.confidence, c.confidence)
                    o.fmt = o.fmt or c.fmt
                    break
            else:
                out.append(Candidate(c.polygon.copy(), c.kind, set(c.methods), c.confidence, c.fmt))
        return out


# --------------------------------------------------------------------------- misc helpers
def _runs(b: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], b.astype(np.int8), [0]]))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def _order_poly(pts: np.ndarray, theta: float) -> np.ndarray:
    """Order 4 rectangle corners so the first edge runs along ``theta``."""
    pts = np.asarray(pts, np.float64)
    ctr = pts.mean(0)
    c, s = math.cos(theta), math.sin(theta)
    rel = pts - ctr
    u = rel @ np.array([c, s])
    v = rel @ np.array([-s, c])
    tl = pts[np.argmin(u + v)]
    tr = pts[np.argmin(-u + v)]
    br = pts[np.argmax(u + v)]
    bl = pts[np.argmax(-u + v)]
    return np.array([tl, tr, br, bl], np.float32)


# Symbologies used on documents/labels. Exotic ones (DataBar, DXFilmEdge, MaxiCode …) are
# excluded: on noisy or faded scans the decoder "finds" them in text and speckle.
_ALLOWED_1D = ("Code128", "Code39", "Code93", "Codabar", "EAN8", "EAN13", "UPCA", "UPCE", "ITF")
_ALLOWED_2D = ("QRCode", "MicroQRCode", "DataMatrix", "PDF417", "Aztec")


def _zxing_formats(include_2d: bool):
    if zxingcpp is None:
        return None
    try:
        names = _ALLOWED_1D + (_ALLOWED_2D if include_2d else ())
        flags = [getattr(zxingcpp.BarcodeFormat, n) for n in names if hasattr(zxingcpp.BarcodeFormat, n)]
        out = flags[0]
        for fl in flags[1:]:
            out = out | fl
        return out
    except Exception:
        return None  # fall back to all formats + the name filter


_2D = {"QRCode", "MicroQRCode", "RMQRCode", "rMQRCode", "DataMatrix", "Aztec", "PDF417", "MaxiCode"}


def _is_2d_format(fmt: str) -> bool:
    return fmt in _2D or "QR" in fmt or "Matrix" in fmt or "Aztec" in fmt or "PDF417" in fmt


def estimate_dpi(width_px: int, height_px: int) -> float:
    """Guess scan resolution assuming an A4/Letter page when the file carries no DPI."""
    long_in = 11.69 if max(width_px, height_px) / max(1, min(width_px, height_px)) > 1.36 else 11.0
    return max(72.0, max(width_px, height_px) / long_in)
