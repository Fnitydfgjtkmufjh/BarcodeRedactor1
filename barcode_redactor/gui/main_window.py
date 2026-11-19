"""Barcode Redactor main window."""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QRadioButton, QSizePolicy, QSlider, QSplitter, QStyle,
                               QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import __version__
from ..core.documents import SUPPORTED_EXT, DocumentError, open_document
from ..core.models import DocumentEntry
from ..core.processing import Cancelled, Engine, Options, collect_files, pad_px, redact_array, write_report
from .page_view import PageView, numpy_to_qimage
from .theme import apply_theme, colors, system_prefers_dark
from .workers import TaskRunner

log = logging.getLogger(__name__)
PREVIEW_DPI = 120
RESOURCES = Path(__file__).resolve().parent.parent / "resources"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Barcode Redactor")
        icon = RESOURCES / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                                  "BarcodeRedactor", "BarcodeRedactor")
        self.entries: list[DocumentEntry] = []
        self.cur_entry: int = -1
        self.cur_page: int = 0
        self._preview_doc = None
        self._preview_path: Path | None = None
        self._preview_img = None  # numpy image of the current page at PREVIEW_DPI
        self._after_task = None
        self._detect_settings_dirty = False
        self.runner = TaskRunner(self)
        self.runner.busyChanged.connect(self._on_busy_changed)
        self.dark = self.settings.value("dark", None)
        self.dark = system_prefers_dark(QApplication.instance()) if self.dark is None else self.dark in (True, "true")
        apply_theme(QApplication.instance(), self.dark)

        self._build_ui()
        self._load_settings()
        self.setAcceptDrops(True)
        self.resize(1440, 900)
        self._refresh_all()

    # ================================================================== UI construction
    def _std(self, name: str) -> QIcon:
        pm = getattr(QStyle.StandardPixmap, name, None)
        return self.style().standardIcon(pm) if pm is not None else QIcon()

    def _build_ui(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self.act_files = QAction(self._std("SP_FileIcon"), "Select Files", self)
        self.act_files.setShortcut(QKeySequence.StandardKey.Open)
        self.act_folder = QAction(self._std("SP_DirOpenIcon"), "Select Folder", self)
        self.act_clear = QAction(self._std("SP_DialogResetButton"), "Clear List", self)
        self.act_theme = QAction(self._std("SP_DesktopIcon"), "Dark mode", self)
        self.act_theme.setCheckable(True)
        self.act_about = QAction(self._std("SP_MessageBoxInformation"), "About", self)
        self.act_files.triggered.connect(self.select_files)
        self.act_folder.triggered.connect(self.select_folder)
        self.act_clear.triggered.connect(self.clear_list)
        self.act_theme.setChecked(self.dark)
        self.act_theme.toggled.connect(self.set_dark)
        self.act_about.triggered.connect(self.show_about)
        for a in (self.act_files, self.act_folder, self.act_clear):
            tb.addAction(a)
        tb.addSeparator()
        self.btn_detect = self._primary("Detect Barcodes", "SP_FileDialogContentsView", self.detect_barcodes)
        self.btn_redact = self._primary("Redact Barcodes", "SP_DialogApplyButton", self.redact_barcodes)
        self.btn_save = self._primary("Save Redacted Files", "SP_DialogSaveButton", self.save_files)
        for b in (self.btn_detect, self.btn_redact, self.btn_save):
            tb.addWidget(b)
            spacer = QWidget()
            spacer.setFixedWidth(6)
            tb.addWidget(spacer)
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(stretch)
        tb.addAction(self.act_theme)
        tb.addAction(self.act_about)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_file_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.addWidget(self._build_settings_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([330, 820, 290])
        self.setCentralWidget(splitter)

        sb = self.statusBar()
        self.status_label = QLabel("Ready. Add scanned documents to begin.")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(320)
        self.progress.setVisible(False)
        self.btn_cancel = QPushButton(self._std("SP_BrowserStop"), "Cancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self.cancel_task)
        sb.addWidget(self.status_label, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.btn_cancel)

        for key, fn in ((Qt.Key.Key_PageDown, self.next_page), (Qt.Key.Key_PageUp, self.prev_page),
                        (Qt.Key.Key_Delete, self._remove_selected)):
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)

    def _primary(self, text, icon, slot) -> QPushButton:
        b = QPushButton(self._std(icon), text)
        b.setProperty("primary", True)
        b.clicked.connect(slot)
        return b

    def _build_file_panel(self) -> QWidget:
        box = QGroupBox("Documents")
        lay = QVBoxLayout(box)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Pages", "Codes", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAcceptDrops(False)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        lay.addWidget(self.tree)
        self.count_label = QLabel()
        self.count_label.setProperty("muted", True)
        lay.addWidget(self.count_label)
        hint = QLabel("Tip: drag & drop PDF, JPG, PNG or TIFF files or whole folders here.")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        lay.addWidget(hint)
        box.setMinimumWidth(260)
        return box

    def _tool(self, text, icon=None, slot=None, checkable=False, tip="") -> QToolButton:
        b = QToolButton()
        b.setText(text)
        if icon:
            b.setIcon(self._std(icon))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setCheckable(checkable)
        if tip:
            b.setToolTip(tip)
        if slot:
            (b.toggled if checkable else b.clicked).connect(slot)
        return b

    def _build_preview_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        nav = QHBoxLayout()
        self.btn_prev = self._tool("Previous", "SP_ArrowLeft", self.prev_page, tip="Previous page (Page Up)")
        self.page_label = QLabel("No document")
        self.page_label.setMinimumWidth(130)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_next = self._tool("Next", "SP_ArrowRight", self.next_page, tip="Next page (Page Down)")
        self.btn_next_review = self._tool("Next to review", "SP_MessageBoxWarning", self.next_review,
                                          tip="Jump to the next page that needs a human check")
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.page_label)
        nav.addWidget(self.btn_next)
        nav.addWidget(self.btn_next_review)
        nav.addStretch(1)
        self.btn_zoom_out = self._tool("−", slot=lambda: self.view_orig.zoom_by(1 / 1.25), tip="Zoom out (Ctrl+wheel)")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_zoom_in = self._tool("+", slot=lambda: self.view_orig.zoom_by(1.25), tip="Zoom in (Ctrl+wheel)")
        self.btn_fit = self._tool("Fit", slot=lambda: self.view_orig.fit(), tip="Fit page to window")
        for b in (self.btn_zoom_out, self.zoom_label, self.btn_zoom_in, self.btn_fit):
            nav.addWidget(b)
        lay.addLayout(nav)

        edit = QHBoxLayout()
        self.btn_draw = self._tool("Draw box", "SP_FileDialogNewFolder", self._toggle_draw, checkable=True,
                                   tip="Drag on the original page to add a redaction box")
        self.btn_remove = self._tool("Remove box", "SP_TrashIcon", self._remove_selected,
                                     tip="Delete the selected box (Delete key)")
        self.btn_redetect = self._tool("Re-detect page", "SP_BrowserReload", self._redetect_page,
                                       tip="Discard manual changes and run detection on this page again")
        self.chk_reviewed = QCheckBox("Page reviewed")
        self.chk_reviewed.setToolTip("Confirm that you checked this page and every barcode is covered")
        self.chk_reviewed.toggled.connect(self._on_reviewed_toggled)
        for b in (self.btn_draw, self.btn_remove, self.btn_redetect):
            edit.addWidget(b)
        edit.addStretch(1)
        edit.addWidget(self.chk_reviewed)
        lay.addLayout(edit)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        lay.addWidget(self.banner)

        views = QSplitter(Qt.Orientation.Horizontal)
        self.view_orig = PageView("Drop PDF or image files here,\nor use Select Files / Select Folder.")
        self.view_red = PageView("The redacted page will appear here.")
        for title, v in (("Original — detected regions", self.view_orig), ("Redacted preview", self.view_red)):
            c = QWidget()
            cl = QVBoxLayout(c)
            cl.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(title)
            lab.setProperty("muted", True)
            cl.addWidget(lab)
            cl.addWidget(v, 1)
            views.addWidget(c)
        lay.addWidget(views, 1)
        legend = QLabel("<span style='color:#16a34a'>■</span> decoded &nbsp; "
                        "<span style='color:#2563eb'>■</span> visual &nbsp; "
                        "<span style='color:#ea580c'>■</span> low confidence &nbsp; "
                        "<span style='color:#9333ea'>■</span> manual &nbsp; "
                        "<span style='color:#f59e0b'>┅</span> barcode-like, not redacted (double-click to redact)")
        legend.setProperty("muted", True)
        lay.addWidget(legend)

        self.view_orig.regionsEdited.connect(self._on_regions_edited)
        self.view_orig.zoomChanged.connect(self._sync_zoom)
        self.view_red.zoomChanged.connect(self._sync_zoom)
        for a, b in ((self.view_orig, self.view_red), (self.view_red, self.view_orig)):
            a.horizontalScrollBar().valueChanged.connect(b.horizontalScrollBar().setValue)
            a.verticalScrollBar().valueChanged.connect(b.verticalScrollBar().setValue)
        return w

    def _build_settings_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        mode = QGroupBox("Workflow")
        ml = QVBoxLayout(mode)
        self.rb_review = QRadioButton("Review before saving (recommended)")
        self.rb_auto = QRadioButton("Automatic: redact and save in one step")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_review)
        self.mode_group.addButton(self.rb_auto)
        ml.addWidget(self.rb_review)
        ml.addWidget(self.rb_auto)
        lay.addWidget(mode)

        red = QGroupBox("Redaction")
        fl = QFormLayout(red)
        self.cmb_color = QComboBox()
        self.cmb_color.addItems(["White", "Black"])
        self.cmb_color.currentIndexChanged.connect(self._on_redaction_option)
        fl.addRow("Fill colour", self.cmb_color)
        self.spin_pad = QDoubleSpinBox()
        self.spin_pad.setRange(0.0, 15.0)
        self.spin_pad.setSingleStep(0.5)
        self.spin_pad.setSuffix(" mm")
        self.spin_pad.setToolTip("Extra margin added around every detected barcode")
        self.spin_pad.valueChanged.connect(self._on_redaction_option)
        fl.addRow("Padding", self.spin_pad)
        lay.addWidget(red)

        det = QGroupBox("Detection")
        dl = QVBoxLayout(det)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Sensitivity"))
        self.sld_sens = QSlider(Qt.Orientation.Horizontal)
        self.sld_sens.setRange(0, 100)
        self.sld_sens.setToolTip("Low = strict (fewer false alarms). High = aggressive (fewer missed barcodes).")
        self.sens_label = QLabel()
        self.sens_label.setMinimumWidth(70)
        self.sld_sens.valueChanged.connect(self._on_sens_changed)
        srow.addWidget(self.sld_sens, 1)
        srow.addWidget(self.sens_label)
        dl.addLayout(srow)
        self.chk_2d = QCheckBox("Also redact QR / 2D codes")
        self.chk_hri = QCheckBox("Include printed number under barcode")
        self.chk_hri.setToolTip("Extends each barcode box over the human-readable digits printed directly "
                                "below or above it (often the sample ID).")
        self.chk_2d.toggled.connect(self._on_detect_option)
        self.chk_hri.toggled.connect(self._on_detect_option)
        dl.addWidget(self.chk_2d)
        dl.addWidget(self.chk_hri)
        lay.addWidget(det)

        out = QGroupBox("Output")
        ol = QFormLayout(out)
        frow = QHBoxLayout()
        self.ed_out = QLineEdit()
        self.ed_out.setReadOnly(True)
        self.ed_out.setPlaceholderText("Choose a folder…")
        b = QToolButton()
        b.setIcon(self._std("SP_DirOpenIcon"))
        b.setToolTip("Choose output folder")
        b.clicked.connect(self.choose_output)
        frow.addWidget(self.ed_out, 1)
        frow.addWidget(b)
        ol.addRow("Folder", frow)
        self.cmb_dpi = QComboBox()
        self.cmb_dpi.addItems(["Match scan", "200 dpi", "300 dpi", "600 dpi"])
        self.cmb_dpi.setToolTip("Resolution of the page images in output PDFs")
        ol.addRow("PDF resolution", self.cmb_dpi)
        self.cmb_comp = QComboBox()
        self.cmb_comp.addItems(["Auto (small files)", "Lossless (larger)"])
        self.cmb_comp.setToolTip("Auto: B&W pages use lossless CCITT G4, grey/colour pages use JPEG.")
        ol.addRow("PDF compression", self.cmb_comp)
        self.btn_open_out = QPushButton(self._std("SP_DirIcon"), "Open output folder")
        self.btn_open_out.clicked.connect(self.open_output)
        ol.addRow(self.btn_open_out)
        lay.addWidget(out)

        priv = QLabel("🔒 All processing happens on this computer. No document content is uploaded.")
        priv.setWordWrap(True)
        priv.setProperty("muted", True)
        lay.addWidget(priv)
        lay.addStretch(1)
        w.setMinimumWidth(270)
        return w

    # ================================================================== settings
    def _load_settings(self):
        s = self.settings
        (self.rb_auto if s.value("mode", "review") == "auto" else self.rb_review).setChecked(True)
        self.cmb_color.setCurrentIndex(1 if s.value("color", "white") == "black" else 0)
        self.spin_pad.setValue(float(s.value("padding_mm", 1.5)))
        self.sld_sens.setValue(int(s.value("sensitivity", 50)))
        self.chk_2d.setChecked(s.value("redact_2d", "true") in (True, "true"))
        self.chk_hri.setChecked(s.value("hri", "true") in (True, "true"))
        self.cmb_dpi.setCurrentIndex(int(s.value("dpi_index", 0)))
        self.cmb_comp.setCurrentIndex(int(s.value("comp_index", 0)))
        self.ed_out.setText(s.value("output_dir", "") or "")
        self._detect_settings_dirty = False
        self._on_sens_changed(self.sld_sens.value())

    def _save_settings(self):
        s = self.settings
        s.setValue("mode", "auto" if self.rb_auto.isChecked() else "review")
        s.setValue("color", "black" if self.cmb_color.currentIndex() == 1 else "white")
        s.setValue("padding_mm", self.spin_pad.value())
        s.setValue("sensitivity", self.sld_sens.value())
        s.setValue("redact_2d", self.chk_2d.isChecked())
        s.setValue("hri", self.chk_hri.isChecked())
        s.setValue("dpi_index", self.cmb_dpi.currentIndex())
        s.setValue("comp_index", self.cmb_comp.currentIndex())
        s.setValue("output_dir", self.ed_out.text())
        s.setValue("dark", self.dark)

    def options(self) -> Options:
        dpi = [0, 200, 300, 600][self.cmb_dpi.currentIndex()]
        return Options(
            redaction_color="black" if self.cmb_color.currentIndex() == 1 else "white",
            sensitivity=self.sld_sens.value() / 100.0,
            padding_mm=self.spin_pad.value(),
            redact_2d=self.chk_2d.isChecked(),
            include_hri_text=self.chk_hri.isChecked(),
            output_dpi=dpi,
            pdf_compression="lossless" if self.cmb_comp.currentIndex() == 1 else "auto",
        )

    @Slot(int)
    def _on_sens_changed(self, v: int):
        txt = "Strict" if v < 34 else ("Balanced" if v < 67 else "Aggressive")
        self.sens_label.setText(f"{txt} ({v})")
        self._on_detect_option()

    def _on_detect_option(self, *_):
        if any(e.pages for e in self.entries):
            self._detect_settings_dirty = True
            self._set_status("Detection settings changed – click Detect Barcodes to re-scan.")

    def _on_redaction_option(self, *_):
        self._update_redacted_preview()

    # ================================================================== theme / about
    @Slot(bool)
    def set_dark(self, on: bool):
        self.dark = on
        apply_theme(QApplication.instance(), on)
        self._show_page(keep_view=True)

    def show_about(self):
        from ..core.detector import BarcodeDetector

        dec = "ZXing-C++ (available)" if BarcodeDetector.decoder_available() else "not available"
        QMessageBox.about(self, "About Barcode Redactor",
                          f"<b>Barcode Redactor {__version__}</b><br><br>"
                          "Finds and permanently removes barcodes from scanned documents.<br>"
                          f"Barcode decoder: {dec}<br>Visual detector: OpenCV<br><br>"
                          "Everything runs locally; no document content leaves this computer.<br>"
                          "See THIRD_PARTY_LICENSES.md for bundled open-source components.")

    # ================================================================== file list
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.add_paths(paths)

    def select_files(self):
        start = self.settings.value("last_input_dir", "")
        exts = " ".join(f"*{x}" for x in sorted(SUPPORTED_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "Select scanned documents", start,
                                                f"Documents ({exts});;All files (*)")
        if files:
            self.settings.setValue("last_input_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def select_folder(self):
        start = self.settings.value("last_input_dir", "")
        d = QFileDialog.getExistingDirectory(self, "Select a folder of scanned documents", start)
        if d:
            self.settings.setValue("last_input_dir", d)
            self.add_paths([d])

    def add_paths(self, paths):
        if self.runner.busy:
            self._set_status("Please wait for the current task to finish (or cancel it).")
            return
        known = {e.path.resolve() for e in self.entries}
        new = [DocumentEntry(p, base) for p, base in collect_files(paths) if p.resolve() not in known]
        if not new:
            self._set_status("No new supported files found (PDF, JPG, PNG, TIFF).")
            return
        self.entries.extend(new)
        for e in new:
            self.tree.addTopLevelItem(QTreeWidgetItem([e.name, "…", "", e.status]))
        self._refresh_all()
        if self.cur_entry < 0:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        start = len(self.entries) - len(new)
        self._run_task("Counting pages", self._task_index(list(range(start, len(self.entries)))))

    def clear_list(self):
        if self.runner.busy:
            return
        self._close_preview_doc()
        self.entries.clear()
        self.tree.clear()
        self.cur_entry, self.cur_page = -1, 0
        self._refresh_all()
        self._set_status("List cleared.")

    def _update_tree_row(self, i: int):
        it = self.tree.topLevelItem(i)
        if it is None:
            return
        e = self.entries[i]
        n_review = e.pages_needing_review
        status = e.status
        c = colors(self.dark)
        color = c["text"]
        if e.error:
            status, color = "Error", "#dc2626"
        elif n_review and not status.startswith("Saved"):
            status, color = f"Review {n_review} page{'s' if n_review > 1 else ''}", "#d97706"
        elif status.startswith("Saved"):
            color = "#16a34a" if not n_review else "#d97706"
        it.setText(1, str(e.page_count) if e.page_count else ("–" if e.error else "…"))
        it.setText(2, str(e.barcode_count) if e.pages else "")
        it.setText(3, status)
        it.setToolTip(0, str(e.path))
        it.setToolTip(3, e.error or status)
        it.setForeground(3, QBrush(QColor(color)))

    def _refresh_all(self):
        for i in range(len(self.entries)):
            self._update_tree_row(i)
        pages = sum(e.page_count for e in self.entries)
        flagged = sum(e.pages_needing_review for e in self.entries)
        txt = f"{len(self.entries)} file{'s' if len(self.entries) != 1 else ''} · {pages} page{'s' if pages != 1 else ''}"
        if flagged:
            txt += f" · {flagged} to review"
        self.count_label.setText(txt)
        self._update_buttons()

    def _update_buttons(self):
        busy = self.runner.busy
        has = bool(self.entries)
        for w in (self.btn_detect, self.btn_redact, self.btn_save):
            w.setEnabled(has and not busy)
        for a in (self.act_files, self.act_folder):
            a.setEnabled(not busy)
        self.act_clear.setEnabled(has and not busy)
        e = self._entry()
        n = e.page_count if e else 0
        self.btn_prev.setEnabled(e is not None and self.cur_page > 0)
        self.btn_next.setEnabled(e is not None and self.cur_page < n - 1)
        self.btn_next_review.setEnabled(any(x.pages_needing_review for x in self.entries))
        editable = e is not None and n > 0 and not busy and self._preview_img is not None
        self.view_orig.set_editable(editable)
        for w in (self.btn_draw, self.btn_remove, self.btn_redetect, self.chk_reviewed):
            w.setEnabled(editable)
        if not editable:
            self.btn_draw.setChecked(False)
        self.btn_open_out.setEnabled(bool(self.ed_out.text()))

    # ================================================================== preview
    def _entry(self) -> DocumentEntry | None:
        return self.entries[self.cur_entry] if 0 <= self.cur_entry < len(self.entries) else None

    def _on_tree_selection(self, cur, _prev):
        idx = self.tree.indexOfTopLevelItem(cur) if cur is not None else -1
        if idx != self.cur_entry:
            self.cur_entry, self.cur_page = idx, 0
            self._show_page()

    def _close_preview_doc(self):
        if self._preview_doc is not None:
            self._preview_doc.close()
        self._preview_doc, self._preview_path, self._preview_img = None, None, None

    def _show_page(self, keep_view: bool = False):
        e = self._entry()
        self.btn_draw.setChecked(False)
        if e is None:
            self._close_preview_doc()
            self.view_orig.set_page(None)
            self.view_red.set_page(None)
            self.page_label.setText("No document")
            self._set_banner("", "info")
            self._update_buttons()
            return
        try:
            if self._preview_path != e.path:
                self._close_preview_doc()
                self._preview_doc = open_document(e.path)
                self._preview_path = e.path
                e.page_count = e.page_count or self._preview_doc.page_count
            n = self._preview_doc.page_count
            self.cur_page = max(0, min(self.cur_page, n - 1))
            rp = self._preview_doc.render(self.cur_page, PREVIEW_DPI)
            self._preview_img = rp.image
        except Exception as ex:  # corrupt file etc.
            self._close_preview_doc()
            e.error = e.error or str(ex)
            self.view_orig.set_page(None)
            self.view_red.set_page(None)
            self.page_label.setText("Cannot display")
            self._set_banner(f"✖ This file cannot be opened: {ex}", "error")
            self._update_tree_row(self.cur_entry)
            self._update_buttons()
            return
        page = e.page(self.cur_page)
        first = not self.view_orig._has_page
        self.view_orig.set_page(numpy_to_qimage(self._preview_img), page.regions, page.suspects, keep_view=keep_view)
        self._update_redacted_preview(keep_view=keep_view)
        if first or not keep_view:
            QTimer.singleShot(0, self.view_orig.fit)
        self.page_label.setText(f"Page {self.cur_page + 1} of {e.page_count}")
        self.chk_reviewed.blockSignals(True)
        self.chk_reviewed.setChecked(page.reviewed)
        self.chk_reviewed.blockSignals(False)
        self._update_banner()
        self._update_buttons()

    def _update_redacted_preview(self, keep_view: bool = True):
        e = self._entry()
        if e is None or self._preview_img is None:
            return
        page = e.page(self.cur_page)
        opts = self.options()
        img = redact_array(self._preview_img, page.regions, opts.redaction_color, pad_px(opts, PREVIEW_DPI))
        self.view_red.set_page(numpy_to_qimage(img), keep_view=True)
        self.view_red.set_zoom(self.view_orig.zoom, emit=False)
        self.view_red.horizontalScrollBar().setValue(self.view_orig.horizontalScrollBar().value())
        self.view_red.verticalScrollBar().setValue(self.view_orig.verticalScrollBar().value())

    def _set_banner(self, text: str, kind: str):
        self.banner.setText(text)
        self.banner.setVisible(bool(text))
        self.banner.setProperty("banner", kind)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)

    def _update_banner(self):
        e = self._entry()
        if e is None:
            return
        p = e.page(self.cur_page)
        n = len(p.regions)
        if p.error:
            self._set_banner(f"✖ Error on this page: {p.error}", "error")
        elif not p.detected and not p.edited:
            self._set_banner("This page has not been scanned yet. Click Detect Barcodes.", "info")
        elif p.reviewed:
            self._set_banner(f"✔ Reviewed — {n} region{'s' if n != 1 else ''} will be redacted.", "ok")
        elif p.review_required:
            why = "; ".join(p.review_reasons) or "please check"
            self._set_banner(f"⚠ Needs review: {why}. Correct the boxes if needed, then tick "
                             "“Page reviewed”.", "warn")
        else:
            self._set_banner(f"✔ {n} barcode region{'s' if n != 1 else ''} found ({p.method_label}). "
                             "Check the redacted preview.", "ok")

    @Slot(float)
    def _sync_zoom(self, z: float):
        src = self.sender()
        other = self.view_red if src is self.view_orig else self.view_orig
        if abs(other.zoom - z) > 1e-6:
            other.set_zoom(z, emit=False)
        self.zoom_label.setText(f"{int(round(z * 100 * PREVIEW_DPI / 96))}%")

    def prev_page(self):
        if self._entry() and self.cur_page > 0:
            self.cur_page -= 1
            self._show_page(keep_view=True)

    def next_page(self):
        e = self._entry()
        if e and self.cur_page < e.page_count - 1:
            self.cur_page += 1
            self._show_page(keep_view=True)

    def next_review(self):
        if not self.entries:
            return
        order = []
        for k in range(len(self.entries)):
            ei = (max(self.cur_entry, 0) + k) % len(self.entries)
            e = self.entries[ei]
            start = self.cur_page + 1 if (k == 0 and ei == self.cur_entry) else 0
            order += [(ei, pi) for pi in range(start, e.page_count)]
        if self.cur_entry >= 0:
            order += [(self.cur_entry, pi) for pi in range(0, self.cur_page + 1)]
        for ei, pi in order:
            p = self.entries[ei].pages.get(pi)
            if p is not None and p.detected and p.needs_attention:
                self.cur_page = pi
                if ei != self.cur_entry:
                    self.cur_entry = ei
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(self.tree.topLevelItem(ei))
                    self.tree.blockSignals(False)
                self._show_page()
                return
        self._set_status("No more pages need review.")

    # ================================================================== editing
    def _toggle_draw(self, on: bool):
        self.view_orig.set_draw_mode(on)

    def _remove_selected(self):
        if not self.view_orig.delete_selected():
            self._set_status("Click a box on the original page to select it first.")

    @Slot()
    def _on_regions_edited(self):
        e = self._entry()
        if e is None:
            return
        p = e.page(self.cur_page)
        p.regions = self.view_orig.regions()
        p.edited = True
        p.detected = True
        p.reviewed = True  # the user has looked at this page and corrected it
        p.review_reasons = [r for r in p.review_reasons if "not redacted" not in r]
        if p.regions:
            p.review_reasons = [r for r in p.review_reasons if not r.startswith("No barcode")]
        self.chk_reviewed.blockSignals(True)
        self.chk_reviewed.setChecked(True)
        self.chk_reviewed.blockSignals(False)
        self._update_redacted_preview()
        self._update_banner()
        self._update_tree_row(self.cur_entry)
        self._refresh_all()

    @Slot(bool)
    def _on_reviewed_toggled(self, on: bool):
        e = self._entry()
        if e is None:
            return
        e.page(self.cur_page).reviewed = on
        self._update_banner()
        self._refresh_all()

    def _redetect_page(self):
        e = self._entry()
        if e is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            p = e.page(self.cur_page)
            p.edited = False
            eng = Engine(self.options())
            with open_document(e.path) as doc:
                rp = doc.render(self.cur_page, min(doc.native_dpi(self.cur_page), 300))
                eng.detect_rendered(p, rp)
        except Exception as ex:
            QMessageBox.warning(self, "Detection failed", str(ex))
        finally:
            QApplication.restoreOverrideCursor()
        self._show_page(keep_view=True)
        self._refresh_all()

    # ================================================================== tasks
    def _run_task(self, label: str, fn, after=None):
        self._after_task = after
        self._task_label = label
        ok = self.runner.start(fn, self._on_progress, self._on_entry_updated, self._on_task_finished,
                               self._on_task_failed)
        if ok:
            self.progress.setRange(0, 0)
            self.progress.setVisible(True)
            self.btn_cancel.setVisible(True)
            self.btn_cancel.setEnabled(True)
            self._set_status(f"{label}…")

    def cancel_task(self):
        self.runner.cancel()
        self.btn_cancel.setEnabled(False)
        self._set_status("Cancelling…")

    @Slot(bool)
    def _on_busy_changed(self, busy: bool):
        if not busy:
            self.progress.setVisible(False)
            self.btn_cancel.setVisible(False)
            after, self._after_task = self._after_task, None
            self._update_buttons()
            if after is not None:
                QTimer.singleShot(0, after)
        else:
            self._update_buttons()

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, msg: str):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self._set_status(msg)

    @Slot(int)
    def _on_entry_updated(self, i: int):
        self._update_tree_row(i)
        if i == self.cur_entry:
            self._show_page(keep_view=True)

    @Slot(object)
    def _on_task_finished(self, result):
        self._refresh_all()
        if self._entry() is not None:
            self._show_page(keep_view=True)
        if isinstance(result, dict) and result.get("cancelled"):
            self._after_task = None
            self._set_status("Cancelled. Finished work has been kept.")
            return
        msg = result.get("message") if isinstance(result, dict) else None
        self._set_status(msg or "Done.")
        if isinstance(result, dict) and result.get("summary"):
            self._show_summary(result)

    @Slot(str)
    def _on_task_failed(self, tb: str):
        self._after_task = None
        log.error(tb)
        self._refresh_all()
        QMessageBox.critical(self, "Unexpected error", "The task stopped because of an unexpected error:\n\n"
                             + tb.strip().splitlines()[-1])

    def _task_index(self, idxs):
        entries = self.entries

        def fn(w):
            for k, i in enumerate(idxs):
                if w.is_cancelled():
                    break
                e = entries[i]
                try:
                    with open_document(e.path) as d:
                        e.page_count = d.page_count
                except DocumentError as ex:
                    e.error, e.status = str(ex), "Error"
                except Exception as ex:
                    e.error, e.status = f"{type(ex).__name__}: {ex}", "Error"
                w.entryUpdated.emit(i)
                w.progress.emit(k + 1, len(idxs), f"Counting pages: {e.name}")
            return {"message": "Files added. Click Detect Barcodes (or Redact Barcodes) to continue."}

        return fn

    def _task_detect(self, force: bool, opts: Options):
        entries = list(self.entries)

        def fn(w):
            eng = Engine(opts, is_cancelled=w.is_cancelled)
            total = sum(max(e.page_count, 1) for e in entries)
            done = 0
            for i, e in enumerate(entries):
                if not force and e.fully_detected:
                    done += e.page_count
                    continue

                def on_page(entry, pi, i=i):
                    nonlocal done
                    done += 1
                    w.progress.emit(done, total, f"Detecting: {entry.name} — page {pi + 1}/{entry.page_count}")

                try:
                    eng.detect_entry(e, force=force, on_page=on_page)
                except DocumentError:
                    done += 1
                except Cancelled:
                    raise
                except Exception as ex:  # one bad file never stops the batch
                    e.error, e.status = f"{type(ex).__name__}: {ex}", "Error"
                w.entryUpdated.emit(i)
            flagged = sum(e.pages_needing_review for e in entries)
            codes = sum(e.barcode_count for e in entries)
            msg = f"Detection finished: {codes} barcode region(s)."
            msg += f" {flagged} page(s) need review." if flagged else " No pages flagged."
            return {"message": msg}

        return fn

    def _task_save(self, out_dir: Path, opts: Options):
        entries = list(self.entries)

        def fn(w):
            eng = Engine(opts, is_cancelled=w.is_cancelled)
            total = sum(max(e.page_count, 1) for e in entries)
            done, saved, failed = 0, 0, 0
            for i, e in enumerate(entries):
                def on_page(entry, pi, i=i):
                    nonlocal done
                    done += 1
                    w.progress.emit(done, total, f"Saving: {entry.name} — page {pi + 1}/{entry.page_count}")

                try:
                    eng.save_entry(e, out_dir, on_page=on_page)
                    saved += 1
                except Cancelled:
                    raise
                except Exception as ex:  # continue with the remaining files
                    e.error, e.status = e.error or f"{type(ex).__name__}: {ex}", "Error"
                    failed += 1
                    done += max(e.page_count, 1)
                w.entryUpdated.emit(i)
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            report = write_report(entries, out_dir / f"redaction_report_{stamp}.csv")
            flagged = sum(1 for e in entries for p in e.pages.values() if p.review_required and not p.reviewed)
            return {"summary": True, "saved": saved, "failed": failed, "flagged": flagged,
                    "report": str(report), "out_dir": str(out_dir),
                    "message": f"Saved {saved} file(s) to {out_dir}."}

        return fn

    # ================================================================== main actions
    def detect_barcodes(self):
        if not self.entries:
            return
        self._detect_settings_dirty = False
        self._save_settings()
        self._run_task("Detecting barcodes", self._task_detect(True, self.options()))

    def redact_barcodes(self):
        if not self.entries:
            return
        self._save_settings()
        if self.rb_auto.isChecked():
            self.save_files(skip_review_check=True)
            return
        force = self._detect_settings_dirty
        self._detect_settings_dirty = False
        self._run_task("Detecting barcodes", self._task_detect(force, self.options()), after=self._after_redact)

    def _after_redact(self):
        flagged = sum(e.pages_needing_review for e in self.entries)
        if flagged:
            self.next_review()
            self._set_status(f"{flagged} page(s) need your review. Fix the boxes, tick “Page reviewed”, "
                             "then click Save Redacted Files.")
        else:
            self._set_status("All pages have barcode regions. Check the redacted preview, then click Save.")

    def choose_output(self) -> bool:
        start = self.ed_out.text() or str(Path.home() / "Documents")
        d = QFileDialog.getExistingDirectory(self, "Choose where to save redacted files", start)
        if d:
            self.ed_out.setText(d)
            self._save_settings()
            self._update_buttons()
            return True
        return False

    def save_files(self, skip_review_check: bool = False):
        if not self.entries:
            return
        if not self.ed_out.text() and not self.choose_output():
            return
        out_dir = Path(self.ed_out.text())
        if not skip_review_check and self.rb_review.isChecked():
            pending = sum(1 for e in self.entries for i in range(e.page_count)
                          if e.page(i).needs_attention)
            if pending:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle("Pages need review")
                box.setText(f"{pending} page(s) have not been reviewed: no barcode was found, detection was "
                            "uncertain, or the page has not been scanned yet.")
                box.setInformativeText("These pages will be marked “Review required” in the CSV report.")
                review = box.addButton("Review now", QMessageBox.ButtonRole.RejectRole)
                box.addButton("Save anyway", QMessageBox.ButtonRole.AcceptRole)
                box.setDefaultButton(review)
                box.exec()
                if box.clickedButton() is review:
                    self.next_review()
                    return
        self._save_settings()
        self._run_task("Saving redacted files", self._task_save(out_dir, self.options()))

    def _show_summary(self, r: dict):
        box = QMessageBox(self)
        warn = r["failed"] or r["flagged"]
        box.setIcon(QMessageBox.Icon.Warning if warn else QMessageBox.Icon.Information)
        box.setWindowTitle("Redaction finished")
        text = f"Saved {r['saved']} redacted file(s)."
        if r["failed"]:
            text += f"\n{r['failed']} file(s) could not be processed."
        if r["flagged"]:
            text += (f"\n{r['flagged']} page(s) were not confirmed by a reviewer (no barcode found or uncertain)."
                     " Please check them – they are marked in the report.")
        else:
            text += "\nAll pages had barcodes detected or were reviewed."
        box.setText(text)
        box.setInformativeText(f"Report: {r['report']}")
        open_btn = box.addButton("Open output folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            self.open_output()

    def open_output(self):
        if self.ed_out.text():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.ed_out.text()))

    def _set_status(self, text: str):
        self.status_label.setText(text)

    # ================================================================== shutdown
    def closeEvent(self, event):
        if self.runner.busy:
            r = QMessageBox.question(self, "Task running", "A task is still running. Cancel it and quit?")
            if r != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.runner.wait(15000)
        self._save_settings()
        self._close_preview_doc()
        event.accept()
