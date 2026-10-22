"""Визначення типу роботи за назвою файлу, першим рядком і (як запасний варіант) змістом."""

from __future__ import annotations

import re

from app.checkers.header import HeaderParseResult, detect_work_types
from app.issues import IssueCollector
from app.models import WorkType
from app.utils.text import fold_lookalikes

_SQ_CONTENT_RE = re.compile(
    r"(?:питання|запитання)\s+(?:є\s+)?(?:стратегічн|важлив)|суть\s+питання|стратегічн\w*\s+(?:питанн|запитанн)"
)
_HYPOTHESIS_CONTENT_RE = re.compile(r"гіпотеза\s+полягає|моя\s+гіпотеза|ціна\s+питання|планован\w*\s+т")
_HYPOTHESIS_CLAIM_RE = re.compile(r"гіпотеза\s+полягає|моя\s+гіпотеза")


def _types_from(parsed: HeaderParseResult) -> set[WorkType]:
    # Явне позначення наприкінці шапки має пріоритет над згадками в інших сегментах
    if parsed.marker_type:
        return {parsed.marker_type}
    return detect_work_types(parsed.source)


def classify_by_content(body_text: str) -> WorkType:
    text = fold_lookalikes(body_text).lower()
    if _SQ_CONTENT_RE.search(text):
        return WorkType.STRATEGIC_QUESTION
    if _HYPOTHESIS_CONTENT_RE.search(text):
        return WorkType.HYPOTHESIS
    return WorkType.UNKNOWN


def classify_work_type(
    file_header: HeaderParseResult, text_header: HeaderParseResult, body_text: str = ""
) -> WorkType:
    """Тип визначено, якщо всі джерела, що його містять, вказують на один тип.
    Якщо в назві й першому рядку типу немає — визначаємо за змістом (Точка А, Ціна питання, «питання стратегічне»)."""
    from_file = _types_from(file_header)
    from_text = _types_from(text_header)
    if len(from_file) > 1 or len(from_text) > 1:
        return WorkType.UNKNOWN
    combined = from_file | from_text
    if len(combined) == 1:
        return next(iter(combined))
    if not combined and body_text:
        return classify_by_content(body_text)
    return WorkType.UNKNOWN


def check_type_consistency(work_type: WorkType, body_text: str, issues: IssueCollector) -> None:
    """Заявлено «Гіпотезу», а в тексті «Моє питання стратегічне» (або навпаки)."""
    text = fold_lookalikes(body_text).lower()
    if work_type == WorkType.HYPOTHESIS and _SQ_CONTENT_RE.search(text):
        issues.add("work_type_mismatch", "Роботу названо Гіпотезою, але в тексті сформульовано стратегічне питання")
    elif work_type == WorkType.STRATEGIC_QUESTION and _HYPOTHESIS_CLAIM_RE.search(text):
        issues.add("work_type_mismatch", "Роботу названо Стратегічним питанням, але в тексті сформульовано гіпотезу")
