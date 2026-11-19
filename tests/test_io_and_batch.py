"""Document handling: PDFs/images in, permanently redacted files out, CSV report, robustness."""
import csv
import hashlib



import pikepdf
import pytest
from PIL import Image


from barcode_redactor.core.documents import open_document
from barcode_redactor.core.models import DocumentEntry, Region
from barcode_redactor.core.processing import Cancelled, Engine, Options, collect_files, write_report
from barcode_redactor.core.synth import make_lab_report

from .helpers import make_scanned_pdf


def page_images(pg):
    return list((pg.get_images() if hasattr(pg, "get_images") else pg.images).values())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def pdf(tmp_path):
    src = make_scanned_pdf(tmp_path / "report.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as p:  # identifying metadata in the source
        p.docinfo["/Title"] = "Patient Asha Patel - CBC"
        p.docinfo["/Author"] = "City Lab"
        p.save(src)
    return src


def test_pdf_output_is_image_only_and_clean(pdf, tmp_path):
    before = sha(pdf)
    eng = Engine(Options())
    e = DocumentEntry(pdf)
    eng.detect_entry(e)
    assert [len(e.page(i).regions) for i in range(3)] == [1, 1, 1]
    out = eng.save_entry(e, tmp_path / "out")
    assert out.name == "report_redacted.pdf"
    assert sha(pdf) == before  # original never modified
    with pikepdf.open(out) as p, pikepdf.open(pdf) as o:
        assert len(p.pages) == 3
        assert dict(p.docinfo) == {}  # no Title/Author/dates
        assert "/Metadata" not in p.Root
        for pg, og in zip(p.pages, o.pages):
            assert [float(v) for v in pg.MediaBox] == pytest.approx([float(v) for v in og.MediaBox], abs=0.01)
            imgs = page_images(pg)
            assert len(imgs) == 1  # exactly one (redacted) image per page
            assert "/Annots" not in pg
            content = pg.Contents.read_bytes() if not isinstance(pg.Contents, pikepdf.Array) else b""
            assert b"Tj" not in content and b"TJ" not in content  # no text layer
        # B&W scan pages stay bilevel (CCITT G4, small); grey pages are JPEG
        f0 = page_images(p.pages[0])[0].Filter
        assert "CCITTFaxDecode" in str(f0)
    # nothing barcode-like left
    e2 = DocumentEntry(out)
    eng.detect_entry(e2)
    assert e2.barcode_count == 0
    assert all(not e2.page(i).suspects for i in range(3))


def test_rotated_pdf_page_keeps_orientation(pdf, tmp_path):
    with pikepdf.open(pdf, allow_overwriting_input=True) as p:
        p.pages[1].Rotate = 90
        p.save(pdf)
    eng = Engine(Options())
    e = DocumentEntry(pdf)
    out = eng.save_entry(e, tmp_path / "o")
    with pikepdf.open(out) as p:
        mb = [float(v) for v in p.pages[1].MediaBox]
        assert mb[2] > mb[3]  # displayed landscape page stays landscape
    assert e.page(1).regions


def test_images_jpg_png_tiff(tmp_path):
    pg = make_lab_report(300, seed=7)
    Image.fromarray(pg.image).save(tmp_path / "scan.jpg", dpi=(300, 300), quality=80)
    Image.fromarray(pg.image).convert("1").save(tmp_path / "bw.png", dpi=(300, 300))
    frames = [Image.fromarray(make_lab_report(200, seed=s).image).convert("1") for s in (8, 9)]
    frames[0].save(tmp_path / "multi.tiff", save_all=True, append_images=frames[1:], compression="group4",
                   dpi=(200, 200))
    eng = Engine(Options(redaction_color="black"))
    outdir = tmp_path / "out"
    for f, _ in collect_files([tmp_path]):
        e = DocumentEntry(f)
        out = eng.save_entry(e, outdir)
        assert out.stem == f.stem + "_redacted" and out.suffix == f.suffix
        with Image.open(f) as a, Image.open(out) as b:
            assert a.size == b.size  # pixel dimensions preserved
            assert getattr(b, "n_frames", 1) == getattr(a, "n_frames", 1)
            assert round(b.info["dpi"][0]) == round(a.info["dpi"][0])
        assert e.barcode_count == e.page_count
    with Image.open(outdir / "multi_redacted.tiff") as t:
        assert t.mode == "1"  # B&W stays B&W


def test_existing_output_is_never_overwritten(pdf, tmp_path):
    eng = Engine(Options())
    out1 = eng.save_entry(DocumentEntry(pdf), tmp_path / "o")
    out2 = eng.save_entry(DocumentEntry(pdf), tmp_path / "o")
    assert out1 != out2 and out2.name == "report_redacted_2.pdf"
    # same folder as the source is fine, the source survives
    out3 = eng.save_entry(DocumentEntry(pdf), pdf.parent)
    assert out3 != pdf and pdf.exists()


def test_folder_batch_report_and_corrupt_file(tmp_path):
    src = tmp_path / "in"
    (src / "sub").mkdir(parents=True)
    make_scanned_pdf(src / "a.pdf", specs=({"bw": True}, {}), without_barcode_pages=(1,))
    make_scanned_pdf(src / "sub" / "a.pdf", specs=({},))
    (src / "broken.pdf").write_bytes(b"%PDF-1.4 not really")
    (src / "notes.txt").write_text("ignored")
    entries = [DocumentEntry(f, base) for f, base in collect_files([src])]
    assert len(entries) == 3
    eng = Engine(Options())
    out = tmp_path / "out"
    ok = 0
    for e in entries:
        try:
            eng.save_entry(e, out)
            ok += 1
        except Exception:
            assert e.error
    assert ok == 2
    assert (out / "a_redacted.pdf").exists() and (out / "sub" / "a_redacted.pdf").exists()
    rep = write_report(entries, out / "report.csv")
    rows = list(csv.DictReader(open(rep, encoding="utf-8-sig")))
    by = {(r["File"].split("in")[-1], r["Page"]): r for r in rows}
    broken = [r for r in rows if r["File"].endswith("broken.pdf")][0]
    assert broken["Error"] and broken["Review required"] == "Yes"
    blank = [r for r in rows if r["File"].endswith("a.pdf") and "sub" not in r["File"] and r["Page"] == "2"][0]
    assert blank["Barcode regions"] == "0" and blank["Review required"] == "Yes"  # never assumed safe
    first = [r for r in rows if r["File"].endswith("a.pdf") and "sub" not in r["File"] and r["Page"] == "1"][0]
    assert first["Barcode regions"] == "1" and first["Review required"] == "No"
    assert set(rows[0]) >= {"File", "Page", "Barcode regions", "Detection method", "Review required", "Error"}
    assert by


def test_manual_edits_survive_redetection(pdf):
    eng = Engine(Options())
    e = DocumentEntry(pdf)
    eng.detect_entry(e)
    p = e.page(0)
    p.regions = [Region(0.1, 0.1, 0.1, 0.1, method="manual")]
    p.edited = True
    eng.detect_entry(e, force=True)
    assert len(e.page(0).regions) == 1 and e.page(0).regions[0].method == "manual"


def test_cancel_leaves_no_partial_file(pdf, tmp_path):
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 1

    eng = Engine(Options(), is_cancelled=cancelled)
    with pytest.raises(Cancelled):
        eng.save_entry(DocumentEntry(pdf), tmp_path / "o")
    assert not any((tmp_path / "o").glob("*"))


def test_page_render_is_lazy(pdf):
    with open_document(pdf) as d:
        assert d.page_count == 3
        rp = d.render(2, 100)
        assert rp.image.shape[0] == pytest.approx(297 / 25.4 * 100, abs=2)
