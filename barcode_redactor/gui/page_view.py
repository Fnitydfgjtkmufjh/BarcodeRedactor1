"""Zoomable page view with editable redaction rectangles."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView)

from ..core.models import Region

LEFT, RIGHT, TOP, BOTTOM = 1, 2, 4, 8
HANDLE_PX = 7  # grab tolerance for edges, in screen pixels

COLORS = {
    "decoded": QColor("#16a34a"),  # green: read by the decoder
    "visual": QColor("#2563eb"),  # blue: found by visual analysis
    "manual": QColor("#9333ea"),  # purple: drawn / edited by the user
    "low": QColor("#ea580c"),  # orange: low confidence
    "suspect": QColor("#f59e0b"),  # amber dashed: barcode-like, NOT redacted
}


def numpy_to_qimage(img: np.ndarray) -> QImage:
    img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    if img.ndim == 2:
        q = QImage(img.data, w, h, img.strides[0], QImage.Format.Format_Grayscale8)
    else:
        rgb = np.ascontiguousarray(img[..., ::-1])
        q = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
    return q.copy()  # detach from the numpy buffer


def region_color(region: Region, low_conf: float = 0.7) -> QColor:
    if region.method in ("manual",) or "edited" in region.method:
        return COLORS["manual"]
    if region.confidence < low_conf:
        return COLORS["low"]
    if "decoded" in region.method:
        return COLORS["decoded"]
    return COLORS["visual"]


class RegionItem(QGraphicsRectItem):
    """A redaction rectangle that can be moved, resized by its edges, and deleted."""

    def __init__(self, region: Region, rect: QRectF, view: "PageView", polygon: QPolygonF | None = None):
        super().__init__(rect)
        self.region = region
        self.view = view
        self.polygon = polygon  # oriented outline of tilted codes (drawn dashed inside the box)
        self.dirty = False
        self._edges = 0
        self._changed = False
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        editable = view.editable
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, editable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, editable)
        self.restyle()

    def restyle(self):
        col = COLORS["manual"] if self.dirty else region_color(self.region)
        pen = QPen(col, 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        fill = QColor(col)
        fill.setAlpha(45)
        self.setBrush(QBrush(fill))

    def paint(self, painter: QPainter, option, widget=None):
        super().paint(painter, option, widget)
        if self.polygon is not None and not self.dirty:
            pen = QPen(self.pen().color(), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(self.polygon)
        if self.isSelected():
            pen = QPen(QColor("#ffffff"), 1, Qt.PenStyle.DotLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(self.rect())

    # -- edge hit-testing
    def _tol(self) -> float:
        return HANDLE_PX / max(self.view.transform().m11(), 1e-6)

    def _edges_at(self, pos: QPointF) -> int:
        r, t = self.rect(), self._tol()
        e = 0
        if abs(pos.x() - r.left()) <= t:
            e |= LEFT
        elif abs(pos.x() - r.right()) <= t:
            e |= RIGHT
        if abs(pos.y() - r.top()) <= t:
            e |= TOP
        elif abs(pos.y() - r.bottom()) <= t:
            e |= BOTTOM
        return e

    def hoverMoveEvent(self, event):
        if not self.view.editable or self.view.draw_mode:
            self.unsetCursor()
            return super().hoverMoveEvent(event)
        e = self._edges_at(event.pos())
        if e in (LEFT | TOP, RIGHT | BOTTOM):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif e in (RIGHT | TOP, LEFT | BOTTOM):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif e in (LEFT, RIGHT):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif e in (TOP, BOTTOM):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if not self.view.editable or self.view.draw_mode:
            event.ignore()
            return
        self._changed = False
        self._edges = self._edges_at(event.pos()) if event.button() == Qt.MouseButton.LeftButton else 0
        if self._edges:
            if not self.isSelected():
                self.scene().clearSelection()
                self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._edges:
            r = QRectF(self.rect())
            p = event.pos()
            m = 2.0 / max(self.view.transform().m11(), 1e-6)
            if self._edges & LEFT:
                r.setLeft(min(p.x(), r.right() - m))
            if self._edges & RIGHT:
                r.setRight(max(p.x(), r.left() + m))
            if self._edges & TOP:
                r.setTop(min(p.y(), r.bottom() - m))
            if self._edges & BOTTOM:
                r.setBottom(max(p.y(), r.top() + m))
            self.prepareGeometryChange()
            self.setRect(r)
            self._changed = True
            return
        super().mouseMoveEvent(event)
        self._changed = True

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._edges = 0
        if self._changed:
            self._changed = False
            self.dirty = True
            self.restyle()
            self.view.item_changed()

    def scene_rect(self) -> QRectF:
        return self.mapRectToScene(self.rect())


class SuspectItem(QGraphicsRectItem):
    """Barcode-like area the detector did not redact. Double-click to redact it."""

    def __init__(self, region: Region, rect: QRectF):
        super().__init__(rect)
        self.region = region
        pen = QPen(COLORS["suspect"], 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(5)
        self.setToolTip("Barcode-like area that was NOT redacted.\nDouble-click to redact it.")


class PageView(QGraphicsView):
    regionsEdited = Signal()
    zoomChanged = Signal(float)

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.editable = False
        self.draw_mode = False
        self._img_w = self._img_h = 1
        self._zoom = 1.0
        self._rubber: QGraphicsRectItem | None = None
        self._rubber_origin = QPointF()
        self._placeholder = placeholder
        self._has_page = False
        self.set_page(None)

    # ---------------------------------------------------------------- content
    def set_page(self, qimg: QImage | None, regions: list[Region] | None = None,
                 suspects: list[Region] | None = None, keep_view: bool = False):
        sc = self.scene()
        old_zoom = self._zoom
        h_val, v_val = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        sc.clear()
        self._rubber = None
        if qimg is None:
            self._has_page = False
            t = QGraphicsSimpleTextItem(self._placeholder)
            t.setBrush(self.palette().placeholderText())
            sc.addItem(t)
            sc.setSceneRect(t.boundingRect().adjusted(-40, -40, 40, 40))
            self.resetTransform()
            return
        self._has_page = True
        self._img_w, self._img_h = qimg.width(), qimg.height()
        sc.addPixmap(QPixmap.fromImage(qimg)).setZValue(0)
        sc.setSceneRect(QRectF(0, 0, self._img_w, self._img_h))
        for r in regions or []:
            self._add_region_item(r)
        for s in suspects or []:
            sc.addItem(SuspectItem(s, self._to_rect(s)))
        if keep_view:
            self.set_zoom(old_zoom, emit=False)
            self.horizontalScrollBar().setValue(h_val)
            self.verticalScrollBar().setValue(v_val)

    def _to_rect(self, r: Region) -> QRectF:
        return QRectF(r.x * self._img_w, r.y * self._img_h, r.w * self._img_w, r.h * self._img_h)

    def _add_region_item(self, r: Region) -> RegionItem:
        poly = None
        if r.polygon:
            poly = QPolygonF([QPointF(x * self._img_w, y * self._img_h) for x, y in r.polygon])
        it = RegionItem(r, self._to_rect(r), self, poly)
        self.scene().addItem(it)
        return it

    def regions(self) -> list[Region]:
        """Current regions in normalised coordinates (applies the user's edits)."""
        out = []
        W, H = float(self._img_w), float(self._img_h)
        for it in self.scene().items():
            if not isinstance(it, RegionItem):
                continue
            if it.dirty:
                rr = it.scene_rect().intersected(QRectF(0, 0, W, H))
                if rr.width() < 1 or rr.height() < 1:
                    continue
                reg = it.region
                reg.set_rect(rr.x() / W, rr.y() / H, rr.width() / W, rr.height() / H)
                if reg.method != "manual" and "edited" not in reg.method:
                    reg.method = reg.method + "+edited"
                reg.confidence = 1.0
            out.append(it.region)
        out.sort(key=lambda r: (r.y, r.x))
        return out

    def set_editable(self, editable: bool):
        self.editable = editable
        for it in self.scene().items():
            if isinstance(it, RegionItem):
                it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, editable)
                it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, editable)
        if not editable:
            self.set_draw_mode(False)

    def set_draw_mode(self, on: bool):
        self.draw_mode = on and self.editable
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if self.draw_mode else Qt.CursorShape.ArrowCursor)

    def item_changed(self):
        self.regionsEdited.emit()

    def delete_selected(self) -> bool:
        if not self.editable:
            return False
        sel = [it for it in self.scene().selectedItems() if isinstance(it, RegionItem)]
        for it in sel:
            self.scene().removeItem(it)
        if sel:
            self.regionsEdited.emit()
        return bool(sel)

    def select_all_regions(self):
        for it in self.scene().items():
            if isinstance(it, RegionItem):
                it.setSelected(True)

    # ---------------------------------------------------------------- zoom
    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, z: float, emit: bool = True):
        z = max(0.05, min(8.0, z))
        self.resetTransform()
        self.scale(z, z)
        self._zoom = z
        if emit:
            self.zoomChanged.emit(z)

    def zoom_by(self, factor: float):
        self.set_zoom(self._zoom * factor)

    def fit(self, emit: bool = True):
        if not self._has_page:
            return
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()
        if emit:
            self.zoomChanged.emit(self._zoom)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            self.zoom_by(1.15 ** steps)
            event.accept()
            return
        super().wheelEvent(event)

    # ---------------------------------------------------------------- drawing
    def mousePressEvent(self, event):
        if self.draw_mode and self.editable and event.button() == Qt.MouseButton.LeftButton and self._has_page:
            self._rubber_origin = self.mapToScene(event.position().toPoint())
            self._rubber = QGraphicsRectItem(QRectF(self._rubber_origin, self._rubber_origin))
            pen = QPen(COLORS["manual"], 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._rubber.setPen(pen)
            self._rubber.setZValue(20)
            self.scene().addItem(self._rubber)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubber is not None:
            p = self.mapToScene(event.position().toPoint())
            self._rubber.setRect(QRectF(self._rubber_origin, p).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._rubber is not None:
            r = self._rubber.rect().intersected(QRectF(0, 0, self._img_w, self._img_h))
            self.scene().removeItem(self._rubber)
            self._rubber = None
            if r.width() * self.zoom >= 4 and r.height() * self.zoom >= 4:
                reg = Region(r.x() / self._img_w, r.y() / self._img_h, r.width() / self._img_w,
                             r.height() / self._img_h, method="manual", confidence=1.0)
                it = self._add_region_item(reg)
                it.restyle()
                self.regionsEdited.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.editable:
            for it in self.items(event.position().toPoint()):
                if isinstance(it, SuspectItem):
                    r = it.region
                    reg = Region(r.x, r.y, r.w, r.h, polygon=r.polygon, method="manual", confidence=1.0)
                    self.scene().removeItem(it)
                    self._add_region_item(reg)
                    self.regionsEdited.emit()
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.delete_selected():
            event.accept()
            return
        super().keyPressEvent(event)
