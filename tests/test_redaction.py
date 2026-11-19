import numpy as np

from barcode_redactor.core.detector import BarcodeDetector
from barcode_redactor.core.models import Region
from barcode_redactor.core.processing import redact_array
from barcode_redactor.core.synth import degrade, make_lab_report


def test_pixels_replaced_white_and_black():
    img = np.zeros((100, 200), np.uint8)
    r = Region(0.25, 0.25, 0.5, 0.5)
    out = redact_array(img, [r], "white", 0)
    assert (out[26:74, 51:149] == 255).all()
    assert out[0, 0] == 0 and (img == 0).all()  # original untouched
    out2 = redact_array(np.full((100, 200, 3), 200, np.uint8), [r], "black", 0)
    assert (out2[30:70, 60:140] == 0).all()


def test_padding_and_polygon():
    img = np.zeros((300, 300), np.uint8)
    poly = np.array([[100, 120], [200, 100], [205, 130], [105, 150]], float)
    r = Region.from_polygon(poly, 300, 300)
    out = redact_array(img, [r], "white", 10)
    assert out[125, 150] == 255  # inside
    assert out[102, 150] == 255  # within the 10 px padding (edge is at y=110)
    assert out[90, 150] == 0  # beyond the padding
    assert out[20, 20] == 0  # far outside stays


def test_redacted_barcode_is_gone():
    for kw in ({}, dict(bw=True, rotate_deg=3), dict(scale_to_dpi=200, jpeg_quality=40)):
        page = degrade(make_lab_report(300, seed=3), seed=3, **kw)
        det = BarcodeDetector()
        res = det.detect(page.image, page.dpi)
        h, w = page.image.shape
        regions = [Region.from_polygon(c.polygon, w, h) for c in res.candidates]
        out = redact_array(page.image, regions, "white", 1.5 / 25.4 * page.dpi)
        again = det.detect(out, page.dpi)
        assert again.candidates == [] and again.near_misses == 0
        # every ground-truth bar pixel is now white
        t = page.truth[0]
        x0, y0 = t.min(0).astype(int) + 2
        x1, y1 = t.max(0).astype(int) - 2
        if not kw.get("rotate_deg"):
            assert (out[y0:y1, x0:x1] == 255).all()
