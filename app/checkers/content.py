"""Змістові перевірки тексту: валюта, пробіли після розділових знаків, биті символи,
текстова (не візуальна) воронка, замалий обсяг роботи."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.issues import IssueCollector
from app.models import WorkType
from app.services.document_loader import ParagraphInfo, ParsedDocument
from app.utils.numbers import find_numbers
from app.utils.text import URL_RE, quote_list

# ------------------------------------------------------------------ currency

_NUM = r"\d[\d .,  ]*"
_USD = r"\$|usd|дол\.|долар\w*|доларів"
_NON_USD = (
    r"грн\.?|гривн\w*|гривен\w*|₴|uah|у\.\s?о\.?|у\.е\.|€|євро|eur\b|euro|zł|злот\w*|pln|"
    r"руб\.|рубл\w*|₽"
)
_NON_USD_AMOUNT_RE = re.compile(rf"(?:{_NUM}\s?(?:{_NON_USD}))|(?:(?:€|₴|₽)\s?{_NUM})", re.IGNORECASE)
_USD_AMOUNT_RE = re.compile(rf"(?:{_NUM}\s?(?:{_USD}))|(?:\$\s?{_NUM})", re.IGNORECASE)

# Не більше кількох згадок іншої валюти (ціна бронювання, зарплата) — прийнятні роботи таке мають
_NON_USD_TOLERANCE = 2


@dataclass
class CurrencyStats:
    usd: int
    non_usd: list[str]


def currency_stats(text: str) -> CurrencyStats:
    return CurrencyStats(
        usd=len(_USD_AMOUNT_RE.findall(text)),
        non_usd=[m.group(0).strip() for m in _NON_USD_AMOUNT_RE.finditer(text)],
    )


def has_currency(text: str) -> bool:
    return bool(re.search(rf"{_USD}|{_NON_USD}", text, re.IGNORECASE))


def has_non_usd(text: str) -> bool:
    return bool(_NON_USD_AMOUNT_RE.search(text))


# Ціна без жодної валюти: «Комплект інсталяції по 7999», «Радіатор за 6400», «Ціна 151», «чек: 1369».
# Число ≥ 100 після «по / за / ціна / вартість / коштує / чек», після якого не стоїть валюта, % чи слово
# («по 100 штук», «за 2024 рік» — не ціни). У прийнятих роботах такого немає.
_BARE_PRICE_RE = re.compile(
    r"(?<![^\W\d_])(?:по|за|ціна|ціною|вартість|вартістю|коштує|коштують|чек)\s*[:–—-]?\s*"
    r"(?:\d{1,3}(?:[  ]\d{3})+|\d{3,})(?:[.,]\d+)?"
    r"(?!\s*(?:\$|%|[^\W\d_]))(?=\s*(?:[,.;:)!?]|$))",
    re.IGNORECASE,
)


def find_bare_prices(paragraphs: list[ParagraphInfo]) -> list[str]:
    return [m.group(0).strip() for p in paragraphs for m in _BARE_PRICE_RE.finditer(p.text)]


# ДЗ№3 — найм персоналу: зарплати в гривнях там природні, у відмовах на це не зважають
_CURRENCY_EXEMPT_DZ = {"3"}


def check_currency(
    doc: ParsedDocument, issues: IssueCollector, work_type: WorkType = WorkType.UNKNOWN, dz_number: str | None = None
) -> None:
    if work_type == WorkType.DZ and dz_number in _CURRENCY_EXEMPT_DZ:
        return
    stats = currency_stats(doc.body_text)
    if len(stats.non_usd) > _NON_USD_TOLERANCE and len(stats.non_usd) >= stats.usd:
        issues.add("wrong_currency", f"Знайдено суми не в доларах: {quote_list(stats.non_usd)}")
    bare = find_bare_prices(doc.body_paragraphs)
    if bare:
        issues.add("missing_currency", f"Ціни без валюти: {quote_list(bare)}")


# --------------------------------------------------------------- punctuation

_EMAIL_RE = re.compile(r"\S+@\S+")
# «слово,слово» / «слово.Слово» — пропущено пробіл після розділового знака
_MISSING_SPACE_RE = re.compile(
    r"(?<=[a-zа-яіїєґ'’]{2})(?:[,;:!?]|\.)(?=[A-Za-zА-ЯІЇЄҐа-яіїєґ]{2})"  # «коронки,накладки», «рейтинги.пацієнти»
    r"|(?<=\S)\s[.,](?=[А-ЯІЇЄҐа-яіїєґ]{2})"  # «20% .Без»
)
_MISSING_SPACE_THRESHOLD = 4


def check_punctuation_spacing(doc: ParsedDocument, issues: IssueCollector) -> None:
    samples: list[str] = []
    for paragraph in doc.body_paragraphs:
        text = _EMAIL_RE.sub(" ", URL_RE.sub(" ", paragraph.text))
        for m in _MISSING_SPACE_RE.finditer(text):
            samples.append(text[max(0, m.start() - 12) : m.end() + 12].strip())
    if len(samples) >= _MISSING_SPACE_THRESHOLD:
        issues.add(
            "missing_space_after_punctuation",
            f"Знайдено {len(samples)} випадків, наприклад: {quote_list(samples, 3)}",
        )


# ------------------------------------------------------------ broken symbols

_SYMBOL_FONTS = ("wingdings", "symbol", "webdings", "marlett")


def check_broken_characters(doc: ParsedDocument, issues: IssueCollector) -> None:
    samples: list[str] = []
    for paragraph in doc.non_empty_paragraphs:
        for run in paragraph.runs:
            text = run.text
            broken = any(ch == "�" or "" <= ch <= "" for ch in text)
            symbol_font = run.font_hansi.casefold().startswith(_SYMBOL_FONTS) and any(ch.isalpha() for ch in text)
            if broken or symbol_font:
                samples.append(text.strip() or text)
    if samples:
        issues.add("broken_characters", f"Фрагменти: {quote_list(samples, 3)}")


# --------------------------------------------------------------- text funnel

_CONVERSION_PHRASE_RE = re.compile(r"\d\s?%\s*(?:від|з|от)\s+\w+", re.IGNORECASE)
_ENUMERATION_RE = re.compile(r"^\d{1,2}[.)]\s*")


def find_text_funnel(paragraphs: list[ParagraphInfo]) -> list[str] | None:
    """Воронка, прописана текстом або таблицею: ≥3 рядки поспіль «Крок  число  [N% від …]»,
    де числа спадають, а щонайменше два рядки містять конверсію у відсотках."""
    run: list[tuple[ParagraphInfo, float]] = []
    best: list[str] | None = None
    for paragraph in paragraphs:
        text = _ENUMERATION_RE.sub("", paragraph.text.strip())  # «1. Опишіть…» — номер пункту, не значення
        numbers = [n for n in find_numbers(text) if not text[n.end : n.end + 2].lstrip().startswith("%")]
        if numbers and len(text) < 120 and re.search(r"[A-Za-zА-Яа-яІіЇїЄєҐґ]{3}", text):
            run.append((paragraph, numbers[0].value))
            continue
        best = _evaluate_run(run) or best
        run = []
    return _evaluate_run(run) or best


def _evaluate_run(run: list[tuple[ParagraphInfo, float]]) -> list[str] | None:
    if len(run) < 3:
        return None
    values = [v for _, v in run]
    decreasing = all(values[i] >= values[i + 1] for i in range(len(values) - 1))
    with_conversion = sum(1 for p, _ in run if _CONVERSION_PHRASE_RE.search(p.text))
    if decreasing and with_conversion >= 2:
        return [p.text.strip() for p, _ in run]
    return None


# -------------------------------------------------------------- short works

# Нижче цих порогів роботу погоджують з куратором (ДЗ№7 — лише посилання на лендинг, це нормально)
SHORT_CONTENT_WORDS = {WorkType.HYPOTHESIS: 80, WorkType.STRATEGIC_QUESTION: 80, WorkType.DZ: 60}


def check_short_content(
    doc: ParsedDocument, work_type: WorkType, dz_number: str | None, issues: IssueCollector
) -> None:
    limit = SHORT_CONTENT_WORDS.get(work_type)
    if limit is None or (work_type == WorkType.DZ and dz_number == "7"):
        return
    words = len(re.findall(r"[^\W\d_]{2,}", doc.body_text))
    if words < limit and not doc.images and not doc.has_vector_drawings:
        issues.add("short_content", f"У роботі лише {words} слів")
