"""Measure missed detections and false positives on simulated print-and-scan pages.

    python tools/evaluate.py --seeds 5 --out docs/EVALUATION.md --samples samples

A barcode counts as *redacted* when ≥97 % of its area is covered by detected regions
(before the user's padding is added). A false positive is a region with <30 % overlap
with any real barcode.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from barcode_redactor.core.detector import BarcodeDetector, DetectorConfig  # noqa: E402
from barcode_redactor.core.models import Region  # noqa: E402
from barcode_redactor.core.processing import redact_array  # noqa: E402
from barcode_redactor.core.synth import (degrade, make_hard_negative, make_lab_report,  # noqa: E402
                                        random_barcode_specs, score_detection)


def multi_specs(seed):
    return random_barcode_specs(seed, 2)


POSITIVE = [
    ("Clean 300 dpi", lambda s: make_lab_report(300, seed=s), {}),
    ("B&W scan", lambda s: make_lab_report(300, seed=s), dict(bw=True, blur_sigma=0.8)),
    ("Low contrast (faded)", lambda s: make_lab_report(300, seed=s), dict(contrast=0.3, noise_std=4)),
    ("Rotated +3°", lambda s: make_lab_report(300, seed=s), dict(rotate_deg=3)),
    ("Rotated −8°", lambda s: make_lab_report(300, seed=s), dict(rotate_deg=-8)),
    ("JPEG q25 + noise", lambda s: make_lab_report(300, seed=s), dict(jpeg_quality=25, noise_std=6)),
    ("Blur σ1.8 + noise", lambda s: make_lab_report(300, seed=s), dict(blur_sigma=1.8, noise_std=12)),
    ("Shadow + skew", lambda s: make_lab_report(300, seed=s), dict(shadow=True, rotate_deg=1.5)),
    ("200 dpi", lambda s: make_lab_report(300, seed=s), dict(scale_to_dpi=200, blur_sigma=0.6)),
    ("200 dpi B&W", lambda s: make_lab_report(300, seed=s), dict(scale_to_dpi=200, bw=True, blur_sigma=1.0)),
    ("600 dpi", lambda s: make_lab_report(300, seed=s), dict(scale_to_dpi=600)),
    ("Small modules 0.19 mm @200 dpi",
     lambda s: make_lab_report(300, seed=s, barcodes=[{"module_mm": 0.19, "height_mm": 6}]),
     dict(scale_to_dpi=200, blur_sigma=0.8, noise_std=6, jpeg_quality=50)),
    ("3 barcodes, random positions", lambda s: make_lab_report(300, seed=s, barcodes=multi_specs(s)),
     dict(rotate_deg=1.2, noise_std=5)),
    ("3 barcodes, 200 dpi B&W", lambda s: make_lab_report(300, seed=s, barcodes=multi_specs(s)),
     dict(scale_to_dpi=200, bw=True, blur_sigma=0.9)),
    ("Vertical barcode", lambda s: make_lab_report(300, seed=s, barcodes=[{"vertical": True, "x_mm": 185,
                                                                           "y_mm": 120}, {}]), dict(noise_std=5)),
    ("Worst case combined", lambda s: make_lab_report(300, seed=s),
     dict(rotate_deg=-2.5, blur_sigma=1.5, noise_std=10, jpeg_quality=30, contrast=0.5, shadow=True)),
    ("Worst case B&W", lambda s: make_lab_report(300, seed=s),
     dict(rotate_deg=2, blur_sigma=1.6, noise_std=12, bw=True, contrast=0.6)),
    ("QR code + barcode", lambda s: make_lab_report(300, seed=s, qr={"text": f"PT-{s:05d}"}), dict(rotate_deg=1)),
]
NEGATIVE = [
    ("Report without barcode", lambda s: make_lab_report(300, seed=100 + s, with_barcode=False), {}),
    ("Report without barcode, B&W 200 dpi", lambda s: make_lab_report(300, seed=100 + s, with_barcode=False),
     dict(scale_to_dpi=200, bw=True)),
    ("Adversarial page (grids, '1111 IIII', hatching)", lambda s: make_hard_negative(300, seed=s), {}),
    ("Adversarial page, B&W", lambda s: make_hard_negative(300, seed=s), dict(bw=True, blur_sigma=0.8)),
]


def overlay(page, res):
    vis = cv2.cvtColor(page.image, cv2.COLOR_GRAY2BGR)
    for c in res.candidates:
        cv2.polylines(vis, [c.polygon.astype(np.int32)], True, (0, 0, 255), max(2, page.dpi // 100))
    for s in res.suspects:
        cv2.polylines(vis, [s.astype(np.int32)], True, (0, 165, 255), max(2, page.dpi // 100))
    return vis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--sensitivity", type=float, default=0.5)
    ap.add_argument("--out", default="docs/EVALUATION.md")
    ap.add_argument("--samples", default="")
    a = ap.parse_args()
    det = BarcodeDetector(DetectorConfig(sensitivity=a.sensitivity))
    rows, t_all, n_all = [], 0.0, 0
    tot_codes = tot_found = tot_fp = 0
    for name, gen, kw in POSITIVE:
        codes = found = fps = flagged = 0
        covs = []
        for s in range(a.seeds):
            page = degrade(gen(s), seed=s, **kw)
            t = time.time()
            res = det.detect(page.image, page.dpi)
            t_all += time.time() - t
            n_all += 1
            sc = score_detection(page, [c.polygon for c in res.candidates])
            codes += len(sc["coverage"])
            found += sum(c >= 0.97 for c in sc["coverage"])
            covs += sc["coverage"]
            fps += sc["false_positives"]
            flagged += int(bool(res.suspects) or any(c.confidence < 0.7 for c in res.candidates)
                           or not res.candidates)
        tot_codes += codes
        tot_found += found
        tot_fp += fps
        rows.append(f"| {name} | {codes} | {found} | {min(covs):.3f} | {fps} | {flagged}/{a.seeds} |")
    neg_rows = []
    for name, gen, kw in NEGATIVE:
        fps = flagged = 0
        for s in range(a.seeds):
            page = degrade(gen(s), seed=s, **kw)
            res = det.detect(page.image, page.dpi)
            fps += len(res.candidates)
            flagged += 1  # no barcode found -> always sent to review
            if a.samples and s == 0 and "Adversarial" in name and not kw:
                Path(a.samples).mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(Path(a.samples) / "adversarial_page_detections.png"),
                            cv2.resize(overlay(page, res), None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA))
        neg_rows.append(f"| {name} | {a.seeds} | {fps} | {flagged}/{a.seeds} |")

    if a.samples:
        sdet = BarcodeDetector(DetectorConfig(sensitivity=a.sensitivity, include_hri_text=True))
        out = Path(a.samples)
        out.mkdir(parents=True, exist_ok=True)
        for tag, kw in (("bw_scan", dict(bw=True, blur_sigma=0.8, rotate_deg=1.5)),
                        ("worst_case", dict(rotate_deg=-2.5, blur_sigma=1.5, noise_std=10, jpeg_quality=30,
                                            contrast=0.5, shadow=True))):
            page = degrade(make_lab_report(300, seed=11), seed=11, **kw)
            res = sdet.detect(page.image, page.dpi)
            h, w = page.image.shape
            red = redact_array(page.image, [Region.from_polygon(c.polygon, w, h) for c in res.candidates],
                               "white", 1.5 / 25.4 * page.dpi)
            small = lambda im: cv2.resize(im, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)  # noqa: E731
            cv2.imwrite(str(out / f"{tag}_1_input.png"), small(page.image))
            cv2.imwrite(str(out / f"{tag}_2_detected.png"), small(overlay(page, res)))
            cv2.imwrite(str(out / f"{tag}_3_redacted.png"), small(red))
            x0, y0 = page.truth[0].min(0).astype(int) - 80
            x1, y1 = page.truth[0].max(0).astype(int) + 80
            crop = np.hstack([cv2.cvtColor(page.image[y0:y1, x0:x1], cv2.COLOR_GRAY2BGR),
                              np.full((y1 - y0, 20, 3), 128, np.uint8),
                              cv2.cvtColor(red[y0:y1, x0:x1], cv2.COLOR_GRAY2BGR)])
            cv2.imwrite(str(out / f"{tag}_4_closeup_before_after.png"), crop)

    md = [
        "# Detection evaluation (synthetic print-and-scan pages)",
        "",
        f"Generated by `tools/evaluate.py --seeds {a.seeds}` · sensitivity {a.sensitivity} · "
        f"decoder {'ON (zxing-cpp)' if det.decoder_available() else 'OFF (visual detection only)'} · "
        f"mean detection time {t_all / max(n_all, 1):.2f} s/page",
        "",
        "A code counts as redacted when ≥ 97 % of its bar area is covered *before* padding is added "
        "(the default 1.5 mm padding then covers the rest). *Flagged* = page sent to review "
        "(low confidence or barcode-like area left unredacted).",
        "",
        "## Pages with barcodes (missed detections)",
        "",
        "| Condition | Codes | Redacted | Min coverage | False-positive regions | Pages flagged |",
        "|---|---|---|---|---|---|",
        *rows,
        f"| **Total** | **{tot_codes}** | **{tot_found}** | | **{tot_fp}** | |",
        "",
        "## Pages without barcodes (false positives)",
        "",
        "| Condition | Pages | False-positive regions | Pages flagged for review |",
        "|---|---|---|---|",
        *neg_rows,
        "",
        "Pages without any detected barcode are always flagged for review – they are never assumed safe.",
        "",
        "**Limits of this evaluation:** pages are synthetic (rendered Code 128 symbols plus simulated "
        "degradations). Real print-and-scan artefacts (toner spread, paper texture, scanner-specific "
        "thresholding) differ, so validate on a sample of your own scanned documents before relying on the "
        "tool, and keep the review workflow on.",
    ]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
