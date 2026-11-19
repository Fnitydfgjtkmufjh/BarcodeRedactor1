"""Application entry point (GUI by default; ``--self-test`` and ``--cli`` for automation)."""
from __future__ import annotations

import argparse
import logging
import os
import sys


def _fix_std_streams() -> None:
    # A windowed (no-console) executable has no stdout/stderr; some libraries write to them anyway.
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        else:  # Windows consoles default to cp1252: never crash on non-ASCII file names
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


def run_gui() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import __app_name__
    from .gui.main_window import MainWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setOrganizationName("BarcodeRedactor")
    win = MainWindow()

    def excepthook(etype, value, tb):
        import traceback

        logging.getLogger("barcode_redactor").error("".join(traceback.format_exception(etype, value, tb)))
        QMessageBox.critical(win, "Unexpected error", f"{etype.__name__}: {value}")

    sys.excepthook = excepthook
    win.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:  # files passed on the command line / dropped onto the .exe
        win.add_paths(args)
    return app.exec()


def run_cli(argv: list[str]) -> int:
    from pathlib import Path

    from .core.models import DocumentEntry
    from .core.processing import Engine, Options, collect_files, write_report

    p = argparse.ArgumentParser(prog="BarcodeRedactor --cli")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--color", choices=["white", "black"], default="white")
    p.add_argument("--sensitivity", type=float, default=0.5)
    p.add_argument("--padding-mm", type=float, default=1.5)
    p.add_argument("--no-2d", action="store_true")
    p.add_argument("--no-hri", action="store_true", help="do not cover the printed number under the barcode")
    a = p.parse_args(argv)
    opts = Options(redaction_color=a.color, sensitivity=a.sensitivity, padding_mm=a.padding_mm,
                   redact_2d=not a.no_2d, include_hri_text=not a.no_hri)
    eng = Engine(opts)
    out = Path(a.out)
    entries = [DocumentEntry(f, base) for f, base in collect_files(a.inputs)]
    for e in entries:
        try:
            dst = eng.save_entry(e, out)
            print(f"{e.path} -> {dst}  ({e.barcode_count} regions, {e.pages_needing_review} page(s) to review)")
        except Exception as ex:
            e.error = e.error or str(ex)
            print(f"{e.path}: ERROR {e.error}", file=sys.stderr)
    rep = write_report(entries, out / "redaction_report.csv")
    print(f"Report: {rep}")
    return 0 if all(not e.error for e in entries) else 1


def main(argv: list[str] | None = None) -> int:
    _fix_std_streams()
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if "--version" in argv:
        from . import __version__

        print(__version__)
        return 0
    if "--self-test" in argv:
        from .selftest import run_self_test

        out = None
        if "--out" in argv:
            out = argv[argv.index("--out") + 1]
        return run_self_test(out)
    if argv and argv[0] == "--cli":
        return run_cli(argv[1:])
    return run_gui()
