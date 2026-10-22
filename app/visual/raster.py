"""Воронки та читабельність на растрових зображеннях (OpenCV + OCR)."""

from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from app.visual.layout import (
    Box,
    FunnelAnalysis,
    Token,
    analyze_stages,
    classify_token,
    find_chains,
    group_rows,
    row_value,
)

MIN_IMAGE_SIDE = 120
TARGET_OCR_WIDTH = 1400
REGION_OCR_WORKERS = 4


@dataclass
class PreparedImage:
    image: np.ndarray
    scale: float  # у скільки разів збільшено для OCR


def decode_image(data: bytes) -> np.ndarray | None:
    """Декодує зображення в BGR; прозорий фон замінюється білим."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as pil:
                img = cv2.cvtColor(np.array(pil.convert("RGBA")), cv2.COLOR_RGBA2BGRA)
        except Exception:
            return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32)
        return (rgb * alpha + 255.0 * (1 - alpha)).astype(np.uint8)
    return img


def prepare_for_ocr(img: np.ndarray) -> PreparedImage | None:
    h, w = img.shape[:2]
    if min(h, w) < MIN_IMAGE_SIDE:
        return None
    scale = 1.0
    if w < TARGET_OCR_WIDTH:
        scale = min(3.0, TARGET_OCR_WIDTH / w)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return PreparedImage(image=img, scale=scale)


def detect_blocks(img: np.ndarray) -> list[Box]:
    """Суцільні зафарбовані фігури (блоки воронки), відсортовані зверху вниз."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1]
    mask = ((gray < 200) | (saturation > 50)).astype(np.uint8) * 255

    k = max(5, round(min(h, w) * 0.015))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[Box] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < 0.08 * w or bh < 0.025 * h or bw * bh > 0.9 * w * h:
            continue
        if cv2.contourArea(contour) < 0.004 * w * h:
            continue
        boxes.append(Box(x, y, x + bw, y + bh))
    return sorted(boxes, key=lambda b: b.y1)


# ----------------------------------------------------------- OCR по регіонах
#
# Заміри на реальних воронках-зображеннях (8 воронок, 51 значення, 21 конверсія):
# OCR усього зображення (psm 11) читав 6/51 значень; збільшення ×2, сірий чи Otsu для всього кадру
# результат не покращували, а погіршували. Розпізнавання окремих фрагментів — вміст кожного блоку,
# опис праворуч, конверсія ліворуч на межі блоків — читає майже все. Otsu допомагає лише всередині
# фрагмента з білим текстом на кольорі, тому застосовується тільки коли оригінал не дав чистого числа.

_CLEAN_NUMBER_RE = re.compile(r"^(?:0|[1-9]\d{0,2}(?:[ .,]\d{3})+|[1-9]\d*)$")
_LABEL_WORD_RE = re.compile(r"[\w«»()'’+/-]{3,}")
_PERCENT_TEXT_RE = re.compile(r"\d")


class RegionReader(Protocol):
    def read_region(self, image: np.ndarray, single_line: bool, digits: bool = False) -> str: ...


def _binarize(crop: np.ndarray) -> np.ndarray:
    """Текст — чорний на білому (білі цифри на синьому блоці інвертуються)."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if (th == 255).mean() < 0.5:
        th = 255 - th
    return cv2.copyMakeBorder(th, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)


def _without_frames(crop: np.ndarray) -> np.ndarray:
    """Прибирає рамки текстових полів (довгі горизонтальні й вертикальні лінії) — вони збивають OCR."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, gray.shape[1] // 8), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, gray.shape[0] // 3)))
    lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel) | cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel)
    gray[cv2.dilate(lines, np.ones((3, 3), np.uint8)) > 0] = 255
    return cv2.copyMakeBorder(gray, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)


def _read_value(reader: RegionReader, crop: np.ndarray) -> str | None:
    attempts = (
        lambda: reader.read_region(crop, single_line=True),
        lambda: reader.read_region(_binarize(crop), single_line=True),
        lambda: reader.read_region(_binarize(crop), single_line=True, digits=True),
    )
    for attempt in attempts:
        text = attempt().strip(" |.,")
        if _CLEAN_NUMBER_RE.match(text):
            return text
    return None


def read_funnel_regions(img: np.ndarray, chain: list[Box], reader: RegionReader) -> list[Token]:
    """Токени воронки, прочитані по фрагментах: значення в центрі кожного блоку, опис праворуч у смузі
    блоку, конверсія ліворуч на межі сусідніх блоків. Координати — межі фрагмента."""
    # Tesseract — окремий процес на кожен виклик, тож блоки читаємо паралельно
    with ThreadPoolExecutor(max_workers=REGION_OCR_WORKERS) as pool:
        per_block = pool.map(lambda idx: _read_block(img, chain, idx, reader), range(len(chain)))
        return [token for tokens in per_block for token in tokens]


def _read_block(img: np.ndarray, chain: list[Box], idx: int, reader: RegionReader) -> list[Token]:
    h, w = img.shape[:2]
    tokens: list[Token] = []
    block = chain[idx]

    def token(text: str, left: float, right: float, center_y: float) -> Token:
        # Компактна висота «рядка» в центрі фрагмента: великі рамки фрагментів злипалися б по вертикалі
        half_h = max(5, min(int((block.y2 - block.y1) * 0.15), 30))
        return Token(text, left, center_y - half_h, right, center_y + half_h, 1.0)

    x1, y1, x2, y2 = (int(v) for v in (block.x1, block.y1, block.x2, block.y2))
    cx, bh = (x1 + x2) // 2, y2 - y1
    half = max(int(0.25 * (x2 - x1)), 15)
    cy1, cy2 = y1 + bh // 12, y2 - bh // 12
    if idx == len(chain) - 1 and len(chain) > 1 and (x2 - x1) < 0.75 * chain[idx - 1].width:
        # Нижній блок — трикутник: цифра у верхній частині, а скошені краї потрапляють у кадр
        half = max(int(0.17 * (x2 - x1)), 12)
        cy2 = y1 + int(bh * 0.7)
    value = _read_value(reader, img[cy1:cy2, max(0, cx - half) : cx + half])
    if value:
        tokens.append(token(value, cx - half, cx + half, (cy1 + cy2) / 2))

    if x2 + 4 < w:
        text = reader.read_region(_without_frames(img[y1:y2, x2 + 2 : w]), single_line=False)
        label = " ".join(_LABEL_WORD_RE.findall(text))
        if label:
            tokens.append(token(label, x2 + 2, w, (y1 + y2) / 2))

    if idx + 1 < len(chain):
        nxt = chain[idx + 1]
        boundary = (y2 + int(nxt.y1)) // 2
        left_edge = min(x1, int(nxt.x1)) - 2
        if left_edge > 10:
            top, bottom = max(0, boundary - bh // 2), min(h, boundary + bh // 2)
            text = reader.read_region(_without_frames(img[top:bottom, 0:left_edge]), single_line=False, digits=True)
            text = text.replace(" ", "")
            if _PERCENT_TEXT_RE.search(text):
                # Ліворуч між блоками — конверсія; якщо OCR загубив «%», повертаємо його.
                # Для першого переходу число без % може бути частотою показів — лишаємо як є.
                if "%" not in text and idx > 0:
                    text += "%"
                tokens.append(token(text, 0, left_edge, boundary))
    return tokens


def read_image_funnel(img: np.ndarray, ocr, min_confidence: float = 0.3) -> FunnelAnalysis:
    """Воронка на зображенні: якщо знайдено блоки — читаємо по фрагментах, інакше OCR усього зображення."""
    chains = find_chains(detect_blocks(img))
    if chains and hasattr(ocr, "read_region"):
        chain = max(chains, key=len)
        analysis = analyze_stages(chain, [classify_token(t) for t in read_funnel_regions(img, chain, ocr)])
        analysis.source = "raster"
        if not any(s.value is not None or s.label for s in analysis.stages):
            analysis.unreadable = True
            analysis.blank = blocks_are_blank(img, chain)
        return analysis
    return analyze_image_funnel(img, ocr.read(img), min_confidence)


def blocks_are_blank(img: np.ndarray, chain: list[Box]) -> bool:
    """Блоки суцільно залиті, без жодного напису: «порожня» воронка-шаблон (цифр у центрі немає)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Нижній блок-трикутник не беремо: у виріз потрапляють його скошені краї
    for block in chain[:-1] if len(chain) > 2 else chain:
        x1, y1, x2, y2 = (int(v) for v in (block.x1, block.y1, block.x2, block.y2))
        # Центральна частина блоку: скошені краї трапеції і межі між блоками не враховуємо
        dx, dy = (x2 - x1) // 4, (y2 - y1) // 5
        crop = gray[y1 + dy : y2 - dy, x1 + dx : x2 - dx]
        if crop.size == 0:
            continue
        if (np.abs(crop.astype(np.int16) - int(np.median(crop))) > 40).mean() > 0.003:
            return False
    return True


# Схема (воронка, оргструктура), а не скріншот: кілька окремих зафарбованих блоків, що не займають увесь кадр
DIAGRAM_MIN_BLOCKS = 8
DIAGRAM_COVER_RANGE = (0.1, 0.6)


def is_diagram(img: np.ndarray, blocks: list[Box]) -> bool:
    if find_chains(blocks):
        return True
    h, w = img.shape[:2]
    cover = sum(b.width * b.height for b in blocks) / (w * h)
    return len(blocks) >= DIAGRAM_MIN_BLOCKS and DIAGRAM_COVER_RANGE[0] <= cover <= DIAGRAM_COVER_RANGE[1]


def displayed_text_height_pt(img: np.ndarray, ocr, display_height_pt: float, min_words: int = 5) -> float | None:
    """Медіанна висота слів так, як їх видно в документі: висота на зображенні × масштаб показу.
    Студенти вставляють велику схему й зменшують її під ширину аркуша — текст стає нечитабельним."""
    if not display_height_pt:
        return None
    scale = display_height_pt / img.shape[0]
    heights = sorted(
        t.height * scale
        for t in ocr.read(img)
        if t.confidence >= 0.6 and sum(ch.isalpha() for ch in t.text) >= 3
    )
    if len(heights) < min_words:
        return None
    return heights[len(heights) // 2]


def analyze_image_funnel(img: np.ndarray, tokens: list[Token], min_confidence: float = 0.3) -> FunnelAnalysis:
    h, w = img.shape[:2]
    infos = [classify_token(t) for t in tokens if t.confidence >= min_confidence and t.text.strip()]
    chains = find_chains(detect_blocks(img))

    if chains:
        chain = max(chains, key=len)
        analysis = analyze_stages(chain, infos)
        analysis.source = "raster"
        # Форма воронки є, а прочитати нічого не вдалося — хай подивиться людина
        if not any(s.value is not None or s.label or s.inside_text for s in analysis.stages):
            analysis.unreadable = True
        return analysis

    # Блоки не розпізнано як окремі фігури (злиті, без заливки) — будуємо кроки з рядків значень
    values = [i for i in infos if i.value is not None and i.percent is None]
    if not values:
        return FunnelAnalysis()
    median_cx = float(np.median([i.token.cx for i in values]))
    central = [i for i in values if abs(i.token.cx - median_cx) <= 0.2 * w]
    rows = [r for r in group_rows(central) if row_value(r)]
    row_values = [row_value(r)[0] for r in rows]
    monotonic = len(row_values) >= 3 and all(row_values[k] >= row_values[k + 1] for k in range(len(row_values) - 1))
    if not monotonic:
        return FunnelAnalysis()
    x1 = min(i.token.x1 for i in central) - 0.03 * w
    x2 = max(i.token.x2 for i in central) + 0.03 * w
    boxes = [Box(x1, min(i.token.y1 for i in r), x2, max(i.token.y2 for i in r)) for r in rows]
    analysis = analyze_stages(boxes, infos)
    analysis.source = "raster"
    return analysis
