"""Plain data model shared by the engine and the GUI.

Region geometry is stored in *normalised page coordinates* (0..1), so the
same region applies to the preview (low DPI), detection render and the final
output render regardless of their resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Region:
    x: float
    y: float
    w: float
    h: float
    # Optional oriented outline (normalised, 4 points). Used for tilted codes as long as the
    # user has not edited the region; editing makes it an axis-aligned rectangle.
    polygon: list[tuple[float, float]] | None = None
    method: str = "visual"  # decoded | visual | decoded+visual | manual | suggested
    kind: str = "1d"  # 1d | 2d
    confidence: float = 1.0
    fmt: str = ""  # symbology name only – decoded content is never kept

    @classmethod
    def from_polygon(cls, poly_px: np.ndarray, width: int, height: int, **kw) -> "Region":
        p = np.asarray(poly_px, np.float64).reshape(-1, 2)
        nx, ny = p[:, 0] / width, p[:, 1] / height
        x0, y0 = float(nx.min()), float(ny.min())
        return cls(x0, y0, float(nx.max()) - x0, float(ny.max()) - y0,
                   polygon=[(float(a), float(b)) for a, b in zip(nx, ny)], **kw)

    def polygon_px(self, width: int, height: int) -> np.ndarray:
        if self.polygon:
            p = np.array(self.polygon, np.float64)
        else:
            p = np.array([[self.x, self.y], [self.x + self.w, self.y],
                          [self.x + self.w, self.y + self.h], [self.x, self.y + self.h]], np.float64)
        return p * np.array([width, height], np.float64)

    def set_rect(self, x: float, y: float, w: float, h: float) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self.polygon = None


@dataclass
class PageResult:
    index: int
    regions: list[Region] = field(default_factory=list)
    suspects: list[Region] = field(default_factory=list)  # barcode-like but NOT redacted
    detected: bool = False
    edited: bool = False  # user changed regions -> never overwritten by re-detection
    reviewed: bool = False  # user confirmed this page
    review_reasons: list[str] = field(default_factory=list)
    error: str = ""
    decoder_used: bool = False

    @property
    def review_required(self) -> bool:
        return bool(self.review_reasons) or bool(self.error) or not self.detected

    @property
    def needs_attention(self) -> bool:
        return self.review_required and not self.reviewed

    @property
    def method_label(self) -> str:
        ms: list[str] = []
        for r in self.regions:
            for m in r.method.split("+"):
                if m not in ms:
                    ms.append(m)
        return "+".join(ms) if ms else "none"


@dataclass
class DocumentEntry:
    path: Path
    base_dir: Path | None = None  # set when added via a folder: output mirrors sub-folders
    page_count: int = 0
    pages: dict[int, PageResult] = field(default_factory=dict)
    error: str = ""
    output_path: Path | None = None
    status: str = "Pending"

    @property
    def name(self) -> str:
        return self.path.name

    def page(self, i: int) -> PageResult:
        if i not in self.pages:
            self.pages[i] = PageResult(i)
        return self.pages[i]

    @property
    def barcode_count(self) -> int:
        return sum(len(p.regions) for p in self.pages.values())

    @property
    def pages_needing_review(self) -> int:
        return sum(1 for p in self.pages.values() if p.detected and p.needs_attention)

    @property
    def fully_detected(self) -> bool:
        return self.page_count > 0 and all(self.page(i).detected or self.page(i).edited
                                           for i in range(self.page_count))
