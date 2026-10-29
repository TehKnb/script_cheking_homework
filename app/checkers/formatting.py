"""Перевірка загального оформлення: шрифт, розміри, жирний, колір пакета, формат чисел, орієнтація."""

from __future__ import annotations

import colorsys
import unicodedata

from app.checkers.header import PACKAGE_COLORS, HeaderParseResult
from app.config import Settings
from app.issues import IssueCollector
from app.models import WorkType
from app.services.document_loader import ParagraphInfo, ParsedDocument, RunInfo
from app.utils.numbers import find_badly_formatted_numbers
from app.utils.text import quote_list, shorten

_SEPARATOR_CHARS = set(" \t\n–—-")
_COLOR_NAMES_UA = {"blue": "синім", "red": "червоним", "black": "чорним", "other": "іншим кольором"}
_PACKAGE_COLOR_NAMES = {"blue": "синім", "red": "червоним", "black": "чорним"}

# Частка тексту, яку дозволено набрати іншим шрифтом (вставки, таблиці) — прийняті роботи це мають
_FONT_TOLERANCE_SHARE = 0.03
_FONT_TOLERANCE_CHARS = 30


def check_formatting(
    doc: ParsedDocument,
    first_line: HeaderParseResult | None,
    settings: Settings,
    issues: IssueCollector,
    work_type: WorkType = WorkType.UNKNOWN,
) -> None:
    first = doc.first_paragraph
    if first is None:
        return
    _check_fonts(doc, settings, issues)
    _check_sizes(first, doc.body_paragraphs, settings, issues)
    _check_bold(first, doc.body_paragraphs, first_line, issues)
    if first_line is not None:
        _check_package_color(first, first_line, issues)
    if work_type.value in settings.number_format_work_types:
        _check_numbers(doc, settings.number_format_min_value, issues)
    if doc.landscape_sections:
        issues.add("landscape_orientation")


# ------------------------------------------------------------------- fonts


def _visible(run: RunInfo) -> bool:
    """Є літери чи цифри: лінія з підкреслень «______» чи окреме тире — не текст, розмір не перевіряємо."""
    return any(_is_text_char(ch) for ch in run.text)


def _is_text_char(ch: str) -> bool:
    """Літери та цифри; емодзі й спецсимволи шрифтом не перевіряються (Word підставляє Segoe UI Emoji)."""
    return unicodedata.category(ch)[0] in ("L", "N")


def _check_fonts(doc: ParsedDocument, settings: Settings, issues: IssueCollector) -> None:
    required = settings.required_font.casefold()
    wrong: dict[str, list[str]] = {}
    wrong_chars = 0
    total_chars = 0
    for paragraph in doc.non_empty_paragraphs:
        for run in paragraph.runs:
            chars = [ch for ch in run.text if _is_text_char(ch)]
            if not chars:
                continue
            total_chars += len(chars)
            used: set[str] = set()
            if any(ch.isascii() for ch in chars):
                used.add(run.font_ascii)
            if any(not ch.isascii() for ch in chars):
                used.add(run.font_hansi)
            bad = [f for f in used if f.split(";")[0].strip().casefold() != required]
            if bad:
                wrong_chars += len(chars)
                for font in bad:
                    wrong.setdefault(font.split(";")[0].strip(), []).append(run.text.strip())
    if not wrong:
        return
    if wrong_chars < _FONT_TOLERANCE_CHARS or wrong_chars < total_chars * _FONT_TOLERANCE_SHARE:
        return
    details = "; ".join(f"«{font}» у фрагментах {quote_list(samples, 3)}" for font, samples in wrong.items())
    issues.add("wrong_font", f"Знайдено шрифт {details}")


# ------------------------------------------------------------------- sizes


def _fmt_pt(value: float) -> str:
    return f"{value:g} pt"


def _check_sizes(
    first: ParagraphInfo, body: list[ParagraphInfo], settings: Settings, issues: IssueCollector
) -> None:
    first_sizes = {run.size_pt for run in first.runs if _visible(run)}
    bad_first = sorted(s for s in first_sizes if abs(s - settings.first_line_size_pt) > 0.01)
    if bad_first:
        issues.add("wrong_first_line_size", f"Фактичний розмір: {', '.join(_fmt_pt(s) for s in bad_first)}")

    wrong: dict[float, list[str]] = {}
    # 11 pt у частині тексту в прийнятих роботах трапляється — допускаємо, ловимо 13–14+ і дрібніше 11
    min_size = settings.body_size_pt - settings.body_size_tolerance_pt
    for paragraph in body:
        # Таблиці (звіти, розрахунки) часто зменшують — у прийнятих роботах це допускається
        if paragraph.in_table:
            continue
        for run in paragraph.runs:
            if _visible(run) and not min_size - 0.01 <= run.size_pt <= settings.body_size_pt + 0.01:
                wrong.setdefault(run.size_pt, []).append(run.text.strip())
    if wrong:
        details = "; ".join(
            f"{_fmt_pt(size)} у фрагментах {quote_list(samples, 3)}" for size, samples in sorted(wrong.items())
        )
        issues.add("wrong_body_text_size", f"Знайдено {details}")


# -------------------------------------------------------------------- bold


def _check_bold(
    first: ParagraphInfo,
    body: list[ParagraphInfo],
    header: HeaderParseResult | None,
    issues: IssueCollector,
) -> None:
    allowed: list[tuple[int, int]] = []
    if header is not None:
        allowed = [(f.start, f.end) for f in (header.business, header.package) if f is not None]
        if header.flow_number is not None and header.package is not None:
            allowed.append((header.flow_number.start, header.flow_number.end))

    def must_be_bold(pos: int) -> bool:
        return any(start <= pos < end for start, end in allowed)

    extra_bold: list[str] = []
    missing_bold: list[str] = []
    for run in first.runs:
        extra = missing = False
        for offset, ch in enumerate(run.text):
            if ch in _SEPARATOR_CHARS or not ch.isalnum():
                continue
            expected = must_be_bold(run.start + offset)
            extra |= run.bold and not expected
            missing |= not run.bold and expected
        if extra:
            extra_bold.append(_fragment(run, bold_expected=False, allowed=allowed))
        if missing:
            missing_bold.append(_fragment(run, bold_expected=True, allowed=allowed))

    details: list[str] = []
    if missing_bold:
        details.append(f"не виділено жирним: {quote_list(missing_bold)}")
    if extra_bold:
        details.append(f"зайве виділення жирним у першому рядку: {quote_list(extra_bold)}")
    if details:
        text = "; ".join(details)
        issues.add("wrong_bold_formatting", text[0].upper() + text[1:])


def _fragment(run: RunInfo, bold_expected: bool, allowed: list[tuple[int, int]]) -> str:
    """Частина run-а, що порушує правило (для зрозумілого опису)."""
    chars = []
    for offset, ch in enumerate(run.text):
        pos = run.start + offset
        inside = any(s <= pos < e for s, e in allowed)
        chars.append(ch if inside == bold_expected else " ")
    fragment = " ".join("".join(chars).split()).strip(" –—-")
    return shorten(fragment or run.text.strip(), 50)


# ------------------------------------------------------------- package color


def classify_color(hex_color: str | None) -> str:
    """Відтінок кольору: black / blue / red / other. Відтінки Word («Синій, акцент 1», 156082) — теж сині."""
    if not hex_color:
        return "black"  # auto
    try:
        r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "other"
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v <= 0.3 or s < 0.25 and v <= 0.45:
        return "black"
    if s < 0.35:
        return "other"
    hue = h * 360
    if 185 <= hue <= 260:
        return "blue"
    if hue <= 15 or hue >= 340:
        return "red"
    return "other"


def _check_package_color(first: ParagraphInfo, header: HeaderParseResult, issues: IssueCollector) -> None:
    package = header.package
    if package is None:
        return
    allowed = PACKAGE_COLORS.get(package.value)
    if allowed is None:
        return
    found: set[str] = set()
    for run in first.runs:
        for offset, ch in enumerate(run.text):
            pos = run.start + offset
            if package.start <= pos < package.end and ch.isalnum():
                found.add(classify_color(run.color))
    shown = header.package_info.display if header.package_info else package.value
    expected = " або ".join(_PACKAGE_COLOR_NAMES[c] for c in sorted(allowed, reverse=True))
    if found - allowed:
        actual = ", ".join(_COLOR_NAMES_UA[c] for c in sorted(found - allowed))
        issues.add(
            "wrong_package_color",
            f"Пакет «{shown}» (разом з номером потоку) має бути виділений {expected}, а виділений {actual}",
        )
    elif len(found) > 1:
        issues.add("wrong_package_color", f"Пакет і номер потоку «{shown}» виділені різними кольорами")


# ----------------------------------------------------------------- numbers


def _check_numbers(doc: ParsedDocument, min_value: int, issues: IssueCollector) -> None:
    bad: list[str] = []
    for paragraph in doc.body_paragraphs:
        bad.extend(find_badly_formatted_numbers(paragraph.text, min_value))
    if bad:
        issues.add("wrong_number_format", f"Знайдено: {quote_list(bad)}")
