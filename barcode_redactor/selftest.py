"""Built-in self-test: proves the bundled native libraries (OpenCV, ZXing, PDFium, Pillow, Qt)
load and that a synthetic scanned report is detected and permanently redacted.

Usage: BarcodeRedactor.exe --self-test [--out result.txt]   (exit code 0 = pass)
"""
from __future__ import annotations

import io
import tempfile
import time
import traceback
from pathlib import Path


def run_self_test(out_file: str | None = None) -> int:
    lines: list[str] = []
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        lines.append(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}".rstrip())

    try:
        t0 = time.time()
        import cv2
        import img2pdf
        import numpy as np
        import pypdfium2
        from PIL import Image

        from . import __version__
        from .core.detector import BarcodeDetector
        from .core.models import DocumentEntry
        from .core.processing import Engine, Options
        from .core.synth import degrade, make_lab_report, score_detection

        lines.append(f"Barcode Redactor {__version__}; OpenCV {cv2.__version__}; numpy {np.__version__}")
        check("zxing-cpp decoder available", BarcodeDetector.decoder_available())
        try:
            from PySide6 import __version__ as qtv
            from PySide6.QtWidgets import QApplication  # noqa: F401
            check("PySide6 (Qt) loads", True, qtv)
        except Exception as e:  # pragma: no cover
            check("PySide6 (Qt) loads", False, str(e))

        page = degrade(make_lab_report(300, seed=3), seed=3, rotate_deg=1.5, blur_sigma=1.0, noise_std=6, bw=True)
        res = BarcodeDetector().detect(page.image, page.dpi)
        sc = score_detection(page, [c.polygon for c in res.candidates])
        check("barcode found on synthetic B&W scan", sc["coverage"] and min(sc["coverage"]) > 0.97,
              f"coverage={sc['coverage']} methods={[c.method_label for c in res.candidates]}")
        check("no false positives", sc["false_positives"] == 0)

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            buf = io.BytesIO()
            Image.fromarray(page.image).convert("1").save(buf, "TIFF", compression="group4", dpi=(300, 300))
            src = td / "selftest.pdf"
            src.write_bytes(img2pdf.convert(buf.getvalue()))
            eng = Engine(Options())
            e = DocumentEntry(src)
            outp = eng.save_entry(e, td / "out")
            check("PDF written", outp.exists(), outp.name)
            e2 = DocumentEntry(outp)
            eng.detect_entry(e2)
            check("no barcode left after redaction", e2.barcode_count == 0 and not e2.page(0).suspects)
            pdf = pypdfium2.PdfDocument(str(outp))
            check("page count preserved", len(pdf) == 1)
            pdf.close()
        lines.append(f"elapsed {time.time() - t0:.1f}s")
    except Exception:
        ok = False
        lines.append("[FAIL] exception:\n" + traceback.format_exc())
    lines.append("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    text = "\n".join(lines)
    if out_file:
        Path(out_file).write_text(text, encoding="utf-8")
    try:
        print(text)
    except Exception:
        pass
    return 0 if ok else 1
