import io
from pathlib import Path

import img2pdf
from PIL import Image

from barcode_redactor.core.synth import degrade, make_lab_report


def make_scanned_pdf(path: Path, specs=({"bw": True}, {}, {"rotate_deg": 2, "noise_std": 5}), dpi=300,
                     without_barcode_pages=()):
    """Multipage 'scanned' PDF: B&W pages as CCITT G4, grey pages as JPEG (like real scanners)."""
    pages = []
    for i, kw in enumerate(specs):
        page = make_lab_report(dpi, seed=i, with_barcode=i not in without_barcode_pages)
        q = degrade(page, seed=i, **kw)
        pil = Image.fromarray(q.image)
        b = io.BytesIO()
        if kw.get("bw"):
            pil.convert("1").save(b, "TIFF", compression="group4", dpi=(dpi, dpi))
        else:
            pil.save(b, "JPEG", quality=85, dpi=(dpi, dpi))
        pages.append(b.getvalue())
    path.write_bytes(img2pdf.convert(pages))
    return path
