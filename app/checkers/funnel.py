"""Перевірка воронок і читабельності візуальних матеріалів.

Джерела воронок:
* SmartArt і фігури Word — з відрендереного PDF (точні координати, без OCR): перевіряються всі правила;
* зображення — OpenCV знаходить блоки, OCR читає фрагменти (значення, опис, конверсію) окремо.
  Помилки виставляються лише тоді, коли прочитано більшість кроків; нечитабельні зображення
  не вважаються помилкою студента;
* текст/таблиця з кроками і відсотками — така воронка не приймається.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field

from app.checkers.content import find_text_funnel
from app.config import Settings
from app.issues import IssueCollector, as_list
from app.ocr.engine import OcrEngine
from app.services.document_loader import ParsedDocument
from app.utils.numbers import format_number
from app.utils.text import quote_list, shorten
from app.visual.layout import FunnelAnalysis, Token, classify_token, is_conversion_correct
from app.visual.pdf import PdfPage, find_small_text, find_vector_funnels
from app.visual.raster import (
    decode_image,
    detect_blocks,
    displayed_text_height_pt,
    is_diagram,
    prepare_for_ocr,
    read_image_funnel,
)

logger = logging.getLogger(__name__)

# Частка кроків з прочитаним значенням/описом, з якої результатам OCR можна довіряти
MIN_RASTER_READABLE_SHARE = 0.6
# Висота слів на схемі-зображенні так, як її видно в документі. На схемах прийнятих робіт — від 7,7 pt,
# у відхиленій «через дрібний текст» — 2,6–3,5 pt. Скріншоти (кабінет реклами) сюди не потрапляють
SMALL_DIAGRAM_TEXT_PT = 4.0
_PERCENT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%")

# Розташування описів і конверсій у векторних воронках (pt і частки висоти блоку). У прийнятих роботах:
# опис — до 41 pt від блоку, розкид відстаней до 28 pt, зсув від центру блоку до 0,28 висоти;
# конверсія — до 0,19 висоти від межі між блоками, до 55 pt від воронки, розкид до 17 pt
ALIGN_LABEL_MAX_GAP_PT = 60
ALIGN_LABEL_MAX_SPREAD_PT = 35
ALIGN_LABEL_MAX_SHIFT = 0.45
ALIGN_LABEL_COLUMN_PT = 12  # відхилення від спільної лівої межі, за якого опис ще «в колонці»
ALIGN_CONVERSION_MAX_SHIFT = 0.3
ALIGN_CONVERSION_MAX_GAP_PT = 70
ALIGN_CONVERSION_MAX_SPREAD_PT = 30


@dataclass
class FunnelCheckSummary:
    has_funnel: bool
    analyses: list[tuple[str, FunnelAnalysis]]


def check_funnels(
    doc: ParsedDocument,
    ocr: OcrEngine | None,
    pdf_pages: list[PdfPage] | None,
    settings: Settings,
    issues: IssueCollector,
    check_numbers: bool = True,
    require_values: bool = False,
) -> FunnelCheckSummary:
    """require_values — у Гіпотезах і Стратегічних питаннях воронка має містити значення й конверсії
    («гіпотетичну» воронку лише з описами кроків приймають у ДЗ)."""
    analyses: list[tuple[str, FunnelAnalysis]] = []

    text_funnel = find_text_funnel(doc.body_paragraphs)
    if text_funnel:
        issues.add("funnel_not_visual", f"Знайдено: {quote_list(text_funnel, 3)}")

    raster = _raster_funnels(doc, ocr, settings, issues)
    analyses += raster.analyses
    if raster.small_diagrams:
        issues.add(
            "small_text",
            "Текст на схемі в документі ≈ "
            + ", ".join(f"{size:.1f} pt ({title.lower()})" for title, size in raster.small_diagrams),
        )

    if pdf_pages is not None:
        for page_no, analysis in find_vector_funnels(pdf_pages):
            analyses.append((f"Воронка на стор. {page_no}", analysis))
        small = find_small_text(pdf_pages, settings.body_size_pt)
        if small:
            issues.add("small_text", f"Дрібним шрифтом набрано {len(small)} слів, наприклад: {quote_list(small, 5)}")
    if doc.smartart_without_drawing:
        issues.add(
            "funnel_unreadable",
            "SmartArt збережено без готового малюнка (документ створено не в MS Word) — перевірте воронку вручну",
        )
    elif doc.has_vector_funnel_candidate and pdf_pages is None:
        issues.add("funnel_unreadable", "Воронку побудовано фігурами Word/SmartArt, а рендер документа недоступний")

    # Підписи й конверсії до воронки-зображення часто додають текстовими полями Word поверх/поруч картинки —
    # тоді їх немає на самому зображенні, і «відсутність» на картинці нічого не доводить
    overlay = doc.has_vector_drawings
    for title, analysis in analyses:
        _report(title, analysis, settings, issues, check_numbers, require_values, overlay)
    _check_blank_funnels(doc, raster.blank, analyses, issues)
    _check_smartart_pyramids(doc, issues)

    has_funnel = any(a.is_funnel for _, a in analyses)
    # Без OCR не знаємо, чи є воронка на зображеннях, — не стверджуємо, що її немає
    return FunnelCheckSummary(
        has_funnel=has_funnel or raster.unknown or doc.smartart_without_drawing or bool(raster.blank),
        analyses=analyses,
    )


@dataclass
class _RasterResult:
    analyses: list[tuple[str, FunnelAnalysis]] = field(default_factory=list)
    unknown: bool = False  # OCR недоступний — що на зображеннях, невідомо
    blank: list[tuple[str, FunnelAnalysis]] = field(default_factory=list)  # воронки з порожніми блоками
    small_diagrams: list[tuple[str, float]] = field(default_factory=list)  # (назва, розмір тексту в pt)


def _check_blank_funnels(
    doc: ParsedDocument,
    blank: list[tuple[str, FunnelAnalysis]],
    analyses: list[tuple[str, FunnelAnalysis]],
    issues: IssueCollector,
) -> None:
    """Порожня воронка-картинка, а конверсії дописані текстовими полями Word поруч: відсотків у полях більше,
    ніж потрібно воронкам зі значеннями, — отже, конверсію вказано й для воронки без значень."""
    if not blank:
        return
    spare = len(_PERCENT_RE.findall(doc.textbox_text)) - sum(len(a.stages) - 1 for _, a in analyses if a.has_values)
    for title, analysis in blank:
        transitions = len(analysis.stages) - 1
        if spare >= max(2, transitions // 2):
            issues.add("funnel_conversion_without_values", f"{title}: блоки воронки порожні, а конверсію вказано")
            spare -= transitions


def _check_smartart_pyramids(doc: ParsedDocument, issues: IssueCollector) -> None:
    """Описи кроків, вписані у вузли SmartArt-піраміди, Word показує всередині блоків. Рендер LibreOffice
    такі піраміди іноді малює без тексту, тому дивимося в саму модель SmartArt."""
    if issues.has("funnel_labels_inside"):
        return
    for texts in doc.smartart_pyramids:
        described = [
            t for t in texts if classify_token(Token(t, 0, 0, 0, 0)).value is None and sum(ch.isalpha() for ch in t) >= 4
        ]
        if len(texts) >= 3 and len(described) >= max(2, len(texts) / 2):
            issues.add("funnel_labels_inside", f"SmartArt-піраміда: {quote_list([shorten(t, 40) for t in described], 3)}")
            return


def smartart_off_page(doc: ParsedDocument, pdf_pages: list[PdfPage]) -> bool:
    """SmartArt-піраміду зі значеннями не видно на сторінках рендеру: LibreOffice розкладає абзац, до якого вона
    прив'язана, інакше, ніж Word, і піраміда зсувається за край аркуша (або не малюється взагалі).
    Перевіряємо за значеннями з блоків піраміди: на сторінці (у межах аркуша) має бути хоча б половина з них."""
    values = {
        re.sub(r"\s", "", text)
        for texts in doc.smartart_pyramids
        for text in texts
        if re.fullmatch(r"[\d\s.,]+", text) and any(ch.isdigit() for ch in text)
    }
    if len(values) < 2:
        return False
    visible = {
        re.sub(r"\s", "", word.text)
        for page in pdf_pages
        for word in page.words
        if 0 <= word.y1 and word.y2 <= page.height
    }
    return len(values & visible) < len(values) / 2


def _raster_funnels(
    doc: ParsedDocument, ocr: OcrEngine | None, settings: Settings, issues: IssueCollector
) -> _RasterResult:
    result = _RasterResult()
    raster = [img for img in doc.images if img.is_raster]
    if not raster:
        return result
    if ocr is None:
        issues.add("funnel_check_unavailable")
        result.unknown = True
        return result

    for number, image in enumerate(raster, start=1):
        img = decode_image(image.data)
        if img is None:
            logger.info("Skip undecodable image %s", image.name)
            continue
        prepared = prepare_for_ocr(img)
        if prepared is None:
            continue
        title = f"Зображення {number}"
        try:
            analysis = read_image_funnel(prepared.image, ocr, settings.ocr_min_confidence)
            if is_diagram(prepared.image, detect_blocks(prepared.image)):
                size = displayed_text_height_pt(prepared.image, ocr, image.display_height_pt)
                if size is not None and size < SMALL_DIAGRAM_TEXT_PT:
                    result.small_diagrams.append((title, size))
        except Exception:
            logger.exception("OCR failed for %s", image.name)
            continue
        if analysis.is_funnel and analysis.readable_share >= MIN_RASTER_READABLE_SHARE:
            result.analyses.append((title, analysis))
        elif analysis.blank:
            result.blank.append((title, analysis))
    return result


def _report(
    title: str,
    analysis: FunnelAnalysis,
    settings: Settings,
    issues: IssueCollector,
    check_numbers: bool,
    require_values: bool,
    overlay: bool = False,
) -> None:
    if analysis.unreadable:
        issues.add("funnel_unreadable", title)
        return

    vector = analysis.source == "vector"
    # Чи можна стверджувати, що чогось (опису, конверсії) немає
    absence_reliable = vector or not overlay
    stages = analysis.stages
    _detect_numbering(title, analysis, issues)
    if vector and analysis.misplaced_conversions:
        issues.add("wrong_conversion_position", f"{title}: {quote_list(analysis.misplaced_conversions)}")
    if vector and analysis.extra_labels:
        issues.add("funnel_label_count_mismatch", f"{title}: зайві описи {quote_list(analysis.extra_labels)}")
    if vector:
        _check_alignment(title, analysis, issues)
    if vector and analysis.overlapping_fields:
        issues.add(
            "funnel_fields_overlap",
            f"{title}: {quote_list([shorten(t, 30) for t in analysis.overlapping_fields])}",
        )

    unlabeled = [s for s in stages if not s.label]
    inside_only = [s for s in unlabeled if s.inside_text] if vector else []
    if inside_only:
        issues.add("funnel_labels_inside", f"{title}: {quote_list([shorten(s.inside_text, 40) for s in inside_only])}")
    missing_labels = [s for s in unlabeled if s not in inside_only]
    if missing_labels and absence_reliable:
        names = ", ".join(f"крок {stages.index(s) + 1}" for s in missing_labels)
        issues.add("missing_funnel_stage_label", f"{title}: {names}")

    if analysis.is_hypothetical:
        if analysis.stated_conversions and not analysis.side_values:
            issues.add("funnel_conversion_without_values", title)
        elif require_values and absence_reliable and not analysis.stated_conversions and not analysis.side_values:
            issues.add(
                "missing_funnel_conversion",
                f"{title}: у Гіпотезі воронка має містити значення кроків і конверсію між ними",
            )
        return

    # Крок без значення, але з описом, у прийнятих роботах трапляється (останній «Клієнти»); пустий — без нічого
    empty = [s for s in stages if s.value is None and not s.label and not s.inside_text]
    if vector and empty and analysis.has_values:
        issues.add("empty_funnel_steps", f"{title}: " + ", ".join(f"крок {stages.index(s) + 1}" for s in empty))

    values = [s.value for s in stages if s.value is not None]
    # «Покази > охоплення» на першому кроці — норма, тому порядок оцінюємо без першого значення
    if len(values) >= 3 and values[1] < values[-1]:
        issues.add("wrong_funnel_order", f"{title}: {' → '.join(format_number(v) for v in values)}")
        return

    missing: list[str] = []
    wrong: list[str] = []
    for check in analysis.conversions:
        if check.expected is None or check.frequency:
            continue
        # «Охват → Показ: 217 947 ÷ 90 456 × 100% = 240,9%» — видно і кроки, і сам розрахунок
        steps = f"{_step_name(stages, check.upper)} → {_step_name(stages, check.lower)}"
        digits = 2 if abs(check.expected) < 10 else 1
        expected = f"{check.expected:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")
        formula = f"{format_number(check.lower.value)} ÷ {format_number(check.upper.value)} × 100% = {expected}%"
        if check.stated is None:
            missing.append(f"{title} · {steps}: немає конверсії (має бути {formula})")
        elif not is_conversion_correct(check, settings.funnel_conversion_tolerance_pp):
            stated = f"{check.stated:g}".replace(".", ",")
            wrong.append(f"{title} · {steps}: {formula}, а вказано {stated}%")
    if missing and absence_reliable:
        issues.add("missing_funnel_conversion", as_list(missing))
    if wrong:
        issues.add("wrong_funnel_conversion", as_list(wrong))

    # OCR не бачить вузьких пробілів між розрядами («61 866» -> «61866»), тож формат чисел — лише для векторних
    unformatted = [n for n in analysis.unformatted_numbers if int(n) >= settings.number_format_min_value]
    if check_numbers and unformatted and vector:
        issues.add("wrong_number_format", f"{title}: {quote_list(unformatted)}")


def _step_name(stages: list, stage) -> str:
    return shorten(stage.label, 30) if stage.label else f"крок {stages.index(stage) + 1}"


def _check_alignment(title: str, analysis: FunnelAnalysis, issues: IssueCollector) -> None:
    """Описи — навпроти своїх блоків і рівною колонкою, конверсії — рівно на межі між блоками біля воронки.
    Пороги — з запасом над прийнятими роботами (див. константи ALIGN_*)."""
    stages = analysis.stages
    height = statistics.median(s.box.height for s in stages)
    names = {id(s): f"крок {k + 1}" for k, s in enumerate(stages)}

    labelled = [s for s in stages if s.label_box is not None]
    if labelled:
        gaps = {id(s): s.label_box.x1 - s.box.x2 for s in labelled}
        shifted = [s for s in labelled if abs(s.label_box.cy - s.cy) > ALIGN_LABEL_MAX_SHIFT * height]
        problems: list[str] = []
        # Описи рівною вертикальною колонкою біля піраміди, що звужується, — теж рівно, хоч відстань до блоків росте
        column_x = statistics.median(s.label_box.x1 for s in labelled)
        in_column = [s for s in labelled if abs(s.label_box.x1 - column_x) <= ALIGN_LABEL_COLUMN_PT]
        far = [s for s in labelled if gaps[id(s)] > ALIGN_LABEL_MAX_GAP_PT]
        if len(labelled) >= 3 and len(in_column) >= 0.75 * len(labelled):
            outliers = [s for s in labelled if s not in in_column]
            if outliers:
                problems.append("випадають із колонки описів: " + ", ".join(f"«{shorten(s.label, 25)}»" for s in outliers))
        elif far:
            problems.append("далеко від воронки: " + ", ".join(f"«{shorten(s.label, 25)}»" for s in far))
        elif len(gaps) >= 3 and max(gaps.values()) - min(gaps.values()) > ALIGN_LABEL_MAX_SPREAD_PT:
            nearest = min(labelled, key=lambda s: gaps[id(s)])
            farthest = max(labelled, key=lambda s: gaps[id(s)])
            problems.append(
                f"стоять врозкид: «{shorten(nearest.label, 25)}» біля самого блоку, "
                f"а «{shorten(farthest.label, 25)}» — на {gaps[id(farthest)] - gaps[id(nearest)]:.0f} pt далі"
            )
        if shifted:
            problems.append(
                "не навпроти свого блоку: " + ", ".join(f"«{shorten(s.label, 25)}» ({names[id(s)]})" for s in shifted)
            )
        if problems:
            issues.add("funnel_labels_misaligned", f"{title}: " + "; ".join(problems))

    placed = [c for c in analysis.conversions if c.stated_box is not None]
    if not placed:
        return
    off_boundary = [
        c for c in placed
        if abs(c.stated_box.cy - (c.upper.box.y2 + c.lower.box.y1) / 2) > ALIGN_CONVERSION_MAX_SHIFT * height
    ]
    gaps = {id(c): min(c.upper.box.x1, c.lower.box.x1) - c.stated_box.x2 for c in placed}
    far = [c for c in placed if gaps[id(c)] > ALIGN_CONVERSION_MAX_GAP_PT]
    problems = []
    if off_boundary:
        problems.append(
            "не на межі між блоками: "
            + ", ".join(f"«{c.stated:g}%» ({names[id(c.upper)]}–{names[id(c.lower)].split()[-1]})" for c in off_boundary)
        )
    if far:
        problems.append("далеко від воронки: " + ", ".join(f"«{c.stated:g}%»" for c in far))
    elif len(gaps) >= 3 and max(gaps.values()) - min(gaps.values()) > ALIGN_CONVERSION_MAX_SPREAD_PT:
        farthest = max(placed, key=lambda c: gaps[id(c)])
        problems.append(f"стоять врозкид: «{farthest.stated:g}%» далі від воронки, ніж інші")
    if problems:
        issues.add("wrong_conversion_position", f"{title}: " + "; ".join(problems))


def _detect_numbering(title: str, analysis: FunnelAnalysis, issues: IssueCollector) -> None:
    """«10, 9, … 1» у центрі — це нумерація кроків, а не значення. Згори вниз від N до 1 означає,
    що кроки переплутані місцями (перший етап має бути зверху); значення з такої воронки не рахуємо."""
    values = [s.value for s in analysis.stages]
    if len(values) < 3 or any(v is None or v != int(v) or v > 30 for v in values):
        return
    steps = {values[i + 1] - values[i] for i in range(len(values) - 1)}
    if steps not in ({1}, {-1}):
        return
    if steps == {-1}:
        issues.add(
            "wrong_funnel_order",
            f"{title}: кроки пронумеровані згори вниз від {int(values[0])} до {int(values[-1])} — "
            "зверху має бути перший етап, а числові значення з центру воронки потрібно прибрати",
        )
    for stage in analysis.stages:
        stage.value, stage.raw = None, ""
    analysis.conversions = []
