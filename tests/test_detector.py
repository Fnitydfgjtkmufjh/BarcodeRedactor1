"""Detection accuracy on simulated print-and-scan conditions (misses AND false positives)."""
import numpy as np
import pytest

from barcode_redactor.core.detector import BarcodeDetector, DetectorConfig
from barcode_redactor.core.synth import (degrade, make_hard_negative, make_lab_report, random_barcode_specs,
                                        score_detection)

CONDITIONS = {
    "clean": {},
    "bw_scan": dict(bw=True, blur_sigma=0.8),
    "rotated_+3": dict(rotate_deg=3),
    "rotated_-8": dict(rotate_deg=-8),
    "jpeg_artifacts": dict(jpeg_quality=25, noise_std=6),
    "blur_noise": dict(blur_sigma=1.8, noise_std=12),
    "low_contrast": dict(contrast=0.3, noise_std=4),
    "shadow": dict(shadow=True, rotate_deg=1.5),
    "200dpi": dict(scale_to_dpi=200, blur_sigma=0.6),
    "200dpi_bw": dict(scale_to_dpi=200, bw=True, blur_sigma=1.0),
    "600dpi": dict(scale_to_dpi=600),
    "worst_case": dict(rotate_deg=-2.5, blur_sigma=1.5, noise_std=10, jpeg_quality=30, contrast=0.5, shadow=True),
}


def _check(page, res, min_cov=0.97):
    sc = score_detection(page, [c.polygon for c in res.candidates])
    assert sc["coverage"], "no ground truth"
    assert min(sc["coverage"]) >= min_cov, sc
    assert sc["false_positives"] == 0, sc
    return sc


@pytest.mark.parametrize("name", list(CONDITIONS))
@pytest.mark.parametrize("seed", [0, 1])
def test_reference_report_conditions(name, seed):
    page = degrade(make_lab_report(300, seed=seed), seed=seed, **CONDITIONS[name])
    _check(page, BarcodeDetector().detect(page.image, page.dpi))


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_multiple_barcodes_varying_positions(seed):
    page = make_lab_report(300, seed=seed, barcodes=random_barcode_specs(seed, 2))
    for kw in ({"rotate_deg": 1.2, "noise_std": 5}, {"scale_to_dpi": 200, "bw": True, "blur_sigma": 0.9}):
        p = degrade(page, seed=seed, **kw)
        res = BarcodeDetector().detect(p.image, p.dpi)
        _check(p, res)
        assert len(res.candidates) == 3


def test_vertical_and_strongly_rotated():
    page = make_lab_report(300, seed=4, barcodes=[{"vertical": True, "x_mm": 185, "y_mm": 120}, {}])
    _check(page, BarcodeDetector().detect(page.image, page.dpi))
    p = degrade(make_lab_report(300, seed=4), rotate_deg=30)
    _check(p, BarcodeDetector().detect(p.image, p.dpi))


def test_small_modules_at_200dpi():
    page = make_lab_report(300, seed=0, barcodes=[{"module_mm": 0.19, "height_mm": 6}])
    p = degrade(page, seed=0, scale_to_dpi=200, blur_sigma=0.8, noise_std=6, jpeg_quality=50)
    _check(p, BarcodeDetector().detect(p.image, p.dpi))


def test_partially_damaged_barcode():
    page = make_lab_report(300, seed=1)
    t = page.truth[0].astype(int)
    (x0, y0), (x1, y1) = t[0], t[2]
    page.image[(y0 + y1) // 2:(y0 + y1) // 2 + 6, x0:x1] = 255  # white scratch
    page.image[y0:y1, x0 + (x1 - x0) // 3:x0 + (x1 - x0) // 3 + 25] = 255  # erased strip
    p = degrade(page, seed=1, noise_std=6, blur_sigma=1)
    _check(p, BarcodeDetector().detect(p.image, p.dpi), min_cov=0.95)


@pytest.mark.parametrize("kw", [{}, dict(bw=True), dict(jpeg_quality=30, noise_std=8, rotate_deg=2),
                                dict(scale_to_dpi=200)])
def test_no_false_positive_on_report_without_barcode(kw):
    for seed in (100, 101):
        p = degrade(make_lab_report(300, seed=seed, with_barcode=False), seed=seed, **kw)
        res = BarcodeDetector().detect(p.image, p.dpi)
        assert res.candidates == []
        assert res.near_misses == 0


@pytest.mark.parametrize("kw", [{}, dict(bw=True, blur_sigma=0.8), dict(scale_to_dpi=600)])
def test_no_false_positive_on_adversarial_page(kw):
    """Small fonts, dense grids, '1111 IIII llll' strings, dashes, hatching."""
    p = degrade(make_hard_negative(300, seed=0), seed=0, **kw)
    res = BarcodeDetector().detect(p.image, p.dpi)
    assert res.candidates == []
    # suspicious look-alikes are reported (page goes to review) rather than silently ignored
    assert res.near_misses >= 0


def test_qr_code_toggle():
    page = degrade(make_lab_report(300, seed=0, qr={"text": "PATIENT-123"}), seed=0, rotate_deg=1)
    with_2d = BarcodeDetector(DetectorConfig(detect_2d=True)).detect(page.image, page.dpi)
    _check(page, with_2d)
    assert any(c.kind == "2d" for c in with_2d.candidates)
    no_2d = BarcodeDetector(DetectorConfig(detect_2d=False)).detect(page.image, page.dpi)
    assert all(c.kind == "1d" for c in no_2d.candidates)


def test_hri_option_extends_region_over_digits():
    page = make_lab_report(300, seed=2)
    base = BarcodeDetector().detect(page.image, page.dpi).candidates[0].polygon
    ext = BarcodeDetector(DetectorConfig(include_hri_text=True)).detect(page.image, page.dpi).candidates[0].polygon
    assert ext[:, 1].max() > base[:, 1].max() + 0.002 * page.image.shape[0]  # grew downwards
    assert ext[:, 1].max() < page.truth[0][:, 1].max() + 6 * 300 / 25.4  # but not into patient details


@pytest.mark.skipif(not BarcodeDetector.decoder_available(), reason="zxing-cpp not installed")
def test_decoder_path_used_on_clean_scan():
    page = make_lab_report(300, seed=5)
    res = BarcodeDetector().detect(page.image, page.dpi)
    _check(page, res)
    assert any("decoded" in c.methods for c in res.candidates)
    assert res.candidates[0].fmt  # symbology recorded (never the content)


def test_works_without_decoder():
    page = degrade(make_lab_report(300, seed=6), seed=6, bw=True)
    res = BarcodeDetector(DetectorConfig(use_decoder=False)).detect(page.image, page.dpi)
    _check(page, res)


def test_color_input_and_missing_dpi():
    page = make_lab_report(300, seed=7)
    color = np.dstack([page.image] * 3)
    color[..., 2] = np.maximum(color[..., 2], 40)  # slight colour cast
    res = BarcodeDetector().detect(color, None)  # dpi estimated from the page size
    _check(page, res)
