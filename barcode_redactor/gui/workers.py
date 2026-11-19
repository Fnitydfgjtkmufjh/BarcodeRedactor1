"""Background tasks so the window stays responsive during long batches."""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..core.processing import Cancelled


class TaskWorker(QObject):
    progress = Signal(int, int, str)  # done, total, message
    entryUpdated = Signal(int)  # index into the document list
    finished = Signal(object)  # result (dict); {"cancelled": True} when cancelled
    failed = Signal(str)

    def __init__(self, fn: Callable[["TaskWorker"], Any]):
        super().__init__()
        self._fn = fn
        self.cancel_event = threading.Event()

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @Slot()
    def run(self):
        try:
            result = self._fn(self)
            self.finished.emit(result if result is not None else {})
        except Cancelled:
            self.finished.emit({"cancelled": True})
        except Exception:
            self.failed.emit(traceback.format_exc())


class TaskRunner(QObject):
    """Owns one QThread + worker at a time."""

    busyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread: QThread | None = None
        self.worker: TaskWorker | None = None

    @property
    def busy(self) -> bool:
        return self.thread is not None

    def start(self, fn, on_progress, on_entry, on_finished, on_failed) -> bool:
        if self.busy:
            return False
        th = QThread()
        w = TaskWorker(fn)
        w.moveToThread(th)
        th.started.connect(w.run)
        w.progress.connect(on_progress)
        w.entryUpdated.connect(on_entry)
        w.finished.connect(on_finished)
        w.failed.connect(on_failed)
        w.finished.connect(th.quit)
        w.failed.connect(th.quit)
        th.finished.connect(self._cleanup)
        self.thread, self.worker = th, w
        self.busyChanged.emit(True)
        th.start()
        return True

    def cancel(self):
        if self.worker is not None:
            self.worker.cancel_event.set()

    def wait(self, ms: int = 10000):
        if self.thread is not None:
            self.cancel()
            self.thread.wait(ms)

    @Slot()
    def _cleanup(self):
        if self.thread is not None:
            self.thread.deleteLater()
        if self.worker is not None:
            self.worker.deleteLater()
        self.thread = self.worker = None
        self.busyChanged.emit(False)
