"""Headless GUI smoke test (runs in CI with QT_QPA_PLATFORM=offscreen)."""
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from barcode_redactor.core.models import Region  # noqa: E402

from .helpers import make_scanned_pdf  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_idle(app, win, timeout=180):
    t = time.time()
    while (win.runner.busy or win._after_task) and time.time() - t < timeout:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(20):
        app.processEvents()
    assert not win.runner.busy


def test_full_workflow(app, tmp_path):
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "cfg"))
    from barcode_redactor.gui.main_window import MainWindow

    src = tmp_path / "in"
    src.mkdir()
    make_scanned_pdf(src / "lab.pdf", specs=({"bw": True}, {}), without_barcode_pages=(1,))
    (src / "broken.pdf").write_bytes(b"%PDF broken")
    out = tmp_path / "out"

    win = MainWindow()
    win.show()
    win.ed_out.setText(str(out))
    win.add_paths([str(src)])
    wait_idle(app, win)
    assert [e.name for e in win.entries] == ["broken.pdf", "lab.pdf"]
    assert win.entries[1].page_count == 2 and win.entries[0].error

    win.tree.setCurrentItem(win.tree.topLevelItem(1))
    app.processEvents()
    assert win._preview_img is not None

    win.detect_barcodes()
    wait_idle(app, win)
    lab = win.entries[1]
    assert len(lab.page(0).regions) == 1
    assert lab.page(1).review_required and not lab.page(1).regions  # blank page flagged

    # jump to the page needing review and draw a box with the mouse
    win.next_review()
    assert win.cur_entry == 1 and win.cur_page == 1
    for _ in range(10):
        app.processEvents()
    v = win.view_orig
    v.set_zoom(0.5)
    win.btn_draw.setChecked(True)
    assert v.draw_mode
    vp = v.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(40, 40))
    QTest.mouseMove(vp, QPoint(140, 90))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(140, 90))
    app.processEvents()
    p1 = lab.page(1)
    assert len(p1.regions) == 1 and p1.regions[0].method == "manual"
    assert p1.edited and p1.reviewed and not p1.needs_attention

    # programmatic add + delete
    v._add_region_item(Region(0.5, 0.5, 0.1, 0.1, method="manual"))
    v.regionsEdited.emit()
    assert len(p1.regions) == 2
    v.select_all_regions()
    v.delete_selected()
    assert len(p1.regions) == 0

    win.prev_page()
    v.zoom_by(1.25)
    v.fit()
    win.set_dark(True)
    win.set_dark(False)

    summaries = []
    win._show_summary = lambda r: summaries.append(r)
    win.save_files(skip_review_check=True)
    wait_idle(app, win)
    assert (out / "lab_redacted.pdf").exists()
    assert summaries and summaries[0]["saved"] == 1 and summaries[0]["failed"] == 1
    assert list(out.glob("redaction_report_*.csv"))
    win.close()
