"""Аналіз відрендереного PDF: векторні воронки (SmartArt, фігури) та дрібний текст.

PDF рендерить LibreOffice — так ми бачимо документ так само, як його бачить перевіряючий:
SmartArt, фігури й текстові поля з реальними координатами на сторінці.
"""

from __future__ import annotations

import io
import logging
import statistics
from dataclasses import dataclass, field

from app.visual.layout import Box, FunnelAnalysis, Token, analyze_stages, classify_token, find_chains

logger = logging.getLogger(__name__)

# Наскільки далеко від краю блоку може стояти підпис/конверсія (pt). Далі — основний текст документа
SIDE_LIMIT_PT = 90
MIN_SHAPE_WIDTH_PT = 18
MIN_SHAPE_HEIGHT_PT = 8
# Текстове поле (опис/конверсія) «налазить» на воронку, якщо перекриває блоки більше ніж на стільки pt².
# У прийнятій роботі рамка опису зачіпала кут блоку на ~19 pt² (кутик 6×6 pt) — це ще не «закриває воронку»
FIELD_OVERLAP_MIN_PT2 = 60.0


Point = tuple[float, float]


@dataclass
class PdfPage:
    number: int
    width: float
    height: float
    shapes: list[Box]
    words: list[Token]
    frames: list[Box] = field(default_factory=list)  # рамки текстових полів (біла заливка або лише обводка)
    polygons: dict[Box, list[Point]] = field(default_factory=dict)  # точна форма зафарбованих фігур


def read_pdf(pdf_bytes: bytes) -> list[PdfPage]:
    import pdfplumber

    pages: list[PdfPage] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            shapes: list[Box] = []
            polygons: dict[Box, list[Point]] = {}
            frames: list[Box] = []
            for o in page.rects + page.curves:
                box = Box(o["x0"], o["top"], o["x1"], o["bottom"])
                visible = o["top"] >= -1 and o["bottom"] <= page.height + 1
                if (
                    o.get("fill")
                    and _is_colored(o.get("non_stroking_color"))
                    and box.width >= MIN_SHAPE_WIDTH_PT
                    and box.height >= MIN_SHAPE_HEIGHT_PT
                    and not (box.width > 0.9 * page.width and box.height > 0.5 * page.height)
                    # Фігури за межами аркуша не видно (плаваючі об'єкти, що «з'їхали» при рендері)
                    and visible
                ):
                    shapes.append(box)
                    polygons[box] = _polygon(o)
                elif (
                    visible
                    # Рамка поля — біла заливка або видима обводка; біла обводка блоків воронки рамкою не є
                    and (
                        (o.get("fill") and _is_white(o.get("non_stroking_color")))
                        or (o.get("stroke") and not _is_white(o.get("stroking_color")))
                    )
                    and 8 <= box.width <= 0.6 * page.width
                    and 6 <= box.height <= 0.3 * page.height
                ):
                    frames.append(box)
            words = []
            for w in page.extract_words(extra_attrs=["size", "non_stroking_color"]):
                token = Token(w["text"], w["x0"], w["top"], w["x1"], w["bottom"], 1.0, float(w.get("size") or 0))
                # Білий текст на білому аркуші не видно (службові нотатки), а на кольоровому блоці — видно
                if _is_white(w.get("non_stroking_color")) and not any(s.contains(token.cx, token.cy) for s in shapes):
                    continue
                words.append(token)
            words = _merge_percent_signs(words)
            pages.append(PdfPage(number, float(page.width), float(page.height), shapes, words, frames, polygons))
    return pages


def find_vector_funnels(pages: list[PdfPage]) -> list[tuple[int, FunnelAnalysis]]:
    found: list[tuple[int, FunnelAnalysis]] = []
    for page in pages:
        for chain in find_chains(page.shapes):
            height = max(b.height for b in chain)
            top = chain[0].y1 - height
            bottom = chain[-1].y2 + height
            infos = [classify_token(w) for w in page.words if top <= w.cy <= bottom]
            analysis = analyze_stages(chain, infos, side_limit=SIDE_LIMIT_PT)
            analysis.overlapping_fields = _overlapping_fields(page, chain, analysis)
            found.append((page.number, analysis))
    return found


def _overlapping_fields(page: PdfPage, chain: list[Box], analysis: FunnelAnalysis) -> list[str]:
    """Описи й конверсії, чиї текстові поля заходять на блоки воронки (закривають її)."""
    fields = [(s.label, s.label_box) for s in analysis.stages if s.label_box is not None]
    fields += [(f"{c.stated:g}%", c.stated_box) for c in analysis.conversions if c.stated_box is not None]
    blocks = [page.polygons.get(b) or _rect(b) for b in chain]
    found: list[str] = []
    for text, box in fields:
        containing = [f for f in page.frames if f.contains(box.cx, box.cy)]
        region = min(containing, key=lambda f: f.width * f.height) if containing else box
        if sum(_overlap_area(_rect(region), block) for block in blocks) >= FIELD_OVERLAP_MIN_PT2:
            found.append(text)
    return found


def _polygon(obj: dict) -> list[Point]:
    pts = obj.get("pts")
    if pts and len(pts) >= 3:
        return [(float(x), float(y)) for x, y in pts]
    return _rect(Box(obj["x0"], obj["top"], obj["x1"], obj["bottom"]))


def _rect(box: Box) -> list[Point]:
    return [(box.x1, box.y1), (box.x2, box.y1), (box.x2, box.y2), (box.x1, box.y2)]


def _overlap_area(rect: list[Point], polygon: list[Point]) -> float:
    """Площа перетину прямокутника з (опуклою) фігурою: відсікання Сазерленда — Ходжмена + формула Гаусса."""
    x1, y1 = rect[0]
    x2, y2 = rect[2]
    clipped = polygon
    for inside, cross in (
        (lambda p: p[0] >= x1, lambda a, b: _at_x(a, b, x1)),
        (lambda p: p[0] <= x2, lambda a, b: _at_x(a, b, x2)),
        (lambda p: p[1] >= y1, lambda a, b: _at_y(a, b, y1)),
        (lambda p: p[1] <= y2, lambda a, b: _at_y(a, b, y2)),
    ):
        source, clipped = clipped, []
        for i, current in enumerate(source):
            previous = source[i - 1]
            if inside(current):
                if not inside(previous):
                    clipped.append(cross(previous, current))
                clipped.append(current)
            elif inside(previous):
                clipped.append(cross(previous, current))
        if not clipped:
            return 0.0
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(clipped, clipped[1:] + clipped[:1]))) / 2


def _at_x(a: Point, b: Point, x: float) -> Point:
    t = (x - a[0]) / (b[0] - a[0]) if b[0] != a[0] else 0.0
    return (x, a[1] + t * (b[1] - a[1]))


def _at_y(a: Point, b: Point, y: float) -> Point:
    t = (y - a[1]) / (b[1] - a[1]) if b[1] != a[1] else 0.0
    return (a[0] + t * (b[0] - a[0]), y)


def find_small_text(pages: list[PdfPage], body_size_pt: float = 12.0, min_words: int = 15) -> list[str]:
    """Слова, набрані помітно дрібніше за основний текст (оргструктури, таблиці, підписи SmartArt)."""
    sizes = [w.size for p in pages for w in p.words if w.size and any(ch.isalpha() for ch in w.text)]
    if not sizes:
        return []
    # Якщо весь документ відрендерився в іншому масштабі — орієнтуємося на його типовий (верхній квартиль) розмір
    upper = statistics.quantiles(sizes, n=4)[2] if len(sizes) >= 4 else max(sizes)
    reference = min(body_size_pt, upper)
    threshold = min(8.0, 0.65 * reference)
    small = [w.text for p in pages for w in p.words if w.size and w.size < threshold and any(ch.isalpha() for ch in w.text)]
    return small if len(small) >= min_words else []


def _merge_percent_signs(words: list[Token]) -> list[Token]:
    """«0,2 %» рендериться двома словами — склеюємо знак відсотка з числом. У вузькому текстовому полі Word
    переносить «%» на наступний рядок під число («140» / «%») — такий знак теж належить числу."""
    merged: list[Token] = []
    for word in words:
        target = _percent_owner(merged, word) if word.text.startswith("%") else None
        if target is None:
            merged.append(word)
            continue
        prev = merged[target]
        merged[target] = Token(
            prev.text + word.text, prev.x1, min(prev.y1, word.y1), max(prev.x2, word.x2), max(prev.y2, word.y2), 1.0, prev.size
        )
    return merged


def _percent_owner(merged: list[Token], sign: Token) -> int | None:
    """Індекс числа, до якого належить знак «%»: праворуч у тому ж рядку або над ним (перенесений рядок)."""
    for idx in range(len(merged) - 1, max(-1, len(merged) - 6), -1):
        prev = merged[idx]
        if not prev.text[-1:].isdigit():
            continue
        same_line = abs(prev.cy - sign.cy) < 3 and 0 <= sign.x1 - prev.x2 < 6
        wrapped = (
            0 <= sign.y1 - prev.y2 < 0.6 * max(prev.height, 1)
            and prev.x1 - 3 <= sign.x1 <= prev.x2
        )
        if same_line or wrapped:
            return idx
    return None


def _rgb(color) -> tuple[float, float, float] | None:
    if color is None:
        return None
    if isinstance(color, (int, float)):
        return (float(color),) * 3
    if isinstance(color, (list, tuple)):
        values = [float(v) for v in color if isinstance(v, (int, float))]
        if len(values) == 1:
            return (values[0],) * 3
        if len(values) == 3:
            return tuple(values)  # type: ignore[return-value]
        if len(values) == 4:  # CMYK
            c, m, y, k = values
            return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _is_white(color) -> bool:
    rgb = _rgb(color)
    return rgb is not None and min(rgb) > 0.95


def _is_colored(color) -> bool:
    """Заливка, яку видно як блок: не біла (білі — текстові поля з підписами)."""
    rgb = _rgb(color)
    return rgb is not None and min(rgb) <= 0.92
