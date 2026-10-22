"""Обов'язкові блоки Гіпотези, Стратегічного питання та окремих ДЗ.

Формат показників (з шаблону курсу та прийнятих робіт):
    Моя точка А (потік) – 3 000 $
    Точка Б (після базової 7-ми тижневої програми) – 5 000 $      ┐ лише для річного доступу
    Точка Б (клуб) – 10 000 $                                     │ (Річна, ВІП+, КМ+)
    За рахунок чого відбулось зростання/падіння: …                ┘
    Планована т.Б – 13 000 $
    Ціна питання – 36 000 $
Ціна питання = (Планована т.Б − поточне значення) × 12, де поточне значення — остання фактична
точка (т.Б клубу або т.А), як рахують у прийнятих роботах.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.checkers.content import has_currency, has_non_usd
from app.config import Settings
from app.issues import IssueCollector
from app.services.document_loader import ParagraphInfo, ParsedDocument
from app.utils.numbers import NumberMatch, find_numbers, format_number
from app.utils.text import fold_lookalikes, normalize, quote_list

_LEAD = r"^[\s\d.)•·*\-–—]*"
_POINT_A_RE = re.compile(_LEAD + r"(?:моя\s+|мій\s+|наша\s+)?(?:точк[аи]\s*[«\"']?а|т\.\s*а|т\s+а)(?![^\W\d_])")
_POINT_B_RE = re.compile(_LEAD + r"(?:моя\s+)?(?:точк[аи]\s*[«\"']?[бв]|т\.\s*[бв]|т\s+[бв])(?![^\W\d_])")
_PLANNED_B_RE = re.compile(
    _LEAD + r"(?:план\w*|заплан\w*)\s+(?:точк\w*\s*[«\"']?[бв]|т\.?\s*[бв])(?![^\W\d_])"
)
_PRICE_RE = re.compile(_LEAD + r"(?:ціна|цена)\s+(?:питання|вопроса)")
_GROWTH_RE = re.compile(r"за\s+рахунок|завдяки\s+чому|за\s+счет")
_HYPOTHESIS_TEXT_RE = re.compile(r"гіпотез|гипотез")
_QUESTIONS_RE = re.compile(r"(?<!ціна\s)(?<!цена\s)(?:питанн|запитанн|вопрос)|\?")
_STRATEGIC_REASON_RE = re.compile(
    r"(?:питання|запитання)\s+(?:є\s+)?(?:стратегічн|важлив)|стратегічн\w*,?\s+(?:оскільки|тому|бо)|"
    r"(?:питання|запитання)\s+стратегічн"
)
_OPTIONS_RE = re.compile(
    r"варіант|рішенн|чи\s+варто|чи\s+краще|чи\s+потрібно|чи\s+правильн|чи\s+доцільн|на\s+мою\s+думку|пропоную"
)
# Мінімум вільного тексту (без рядків з показниками), щоб вважати, що гіпотезу описано
_MIN_HYPOTHESIS_WORDS = 30
# Мінус: «-2100$» (дефіс впритул до числа) або слово «мінус». «т.Б - 17 000$» — це тире, не мінус
_MINUS_RE = re.compile(r"(?:мінус|minus)\s*[-–]?\s*$|(?:^|[\s(:–—])-$", re.IGNORECASE)
_MONEY_SIGN_AFTER_RE = re.compile(r"^\s?(?:\$|usd|дол|грн|₴|uah|€|євро|у\.\s?о)", re.IGNORECASE)


@dataclass
class MetricLine:
    kind: str  # point_a | point_a_club | point_b | point_b_program | point_b_club | planned_b | price
    text: str
    value: float | None
    has_currency: bool
    non_usd: bool


@dataclass
class Metrics:
    lines: list[MetricLine] = field(default_factory=list)
    growth_reason: bool = False

    def of(self, *kinds: str) -> list[MetricLine]:
        return [line for line in self.lines if line.kind in kinds]

    @property
    def planned(self) -> list[MetricLine]:
        """«Планована т.Б»; у потоці її часто пишуть просто «Моя т.Б – 3 000$»."""
        return self.of("planned_b") or self.of("point_b")

    @property
    def current(self) -> list[MetricLine]:
        """Фактичні точки, від яких рахують ціну питання (т.А, т.Б після програми, т.Б клубу)."""
        kinds = ("point_a", "point_a_club", "point_b_program", "point_b_club")
        if self.of("planned_b"):
            kinds += ("point_b",)
        return [m for m in self.of(*kinds) if m.value is not None]


def _probe(text: str) -> str:
    return fold_lookalikes(normalize(text)).lower()


def _money_value(text: str) -> float | None:
    """Грошове значення рядка: число біля знака валюти (з урахуванням мінуса), інакше останнє «велике» число.
    Дрібні числа без валюти («7-ми тижневої», «клуб 66») — не значення показника."""
    numbers = [n for n in find_numbers(text) if not text[n.end : n.end + 1] == "%"]
    with_currency = [
        n for n in numbers if _MONEY_SIGN_AFTER_RE.match(text[n.end :]) or text[max(0, n.start - 2) : n.start].strip() == "$"
    ]
    if with_currency:
        # «т.А – 1 000$ (при обороті $50 000 – $70 000)» — значення показника стоїть першим
        chosen: NumberMatch = with_currency[0]
    else:
        candidates = [n for n in numbers if abs(n.value) >= 100 or n.has_multiplier]
        if not candidates:
            return None
        chosen = candidates[-1]
    sign = -1 if _MINUS_RE.search(text[max(0, chosen.start - 8) : chosen.start]) else 1
    return sign * chosen.value


def extract_metrics(lines: list[ParagraphInfo]) -> Metrics:
    metrics = Metrics()
    texts = [normalize(p.text) for p in lines]
    open_heading: str | None = None  # «т А в клубі:» — значення йдуть наступними рядками
    for idx, text in enumerate(texts):
        probe = _probe(text)
        if _GROWTH_RE.search(probe):
            metrics.growth_reason = True
        kind: str | None = None
        match = None
        if match := _PRICE_RE.match(probe):
            kind = "price"
        elif (match := _PLANNED_B_RE.match(probe)) or ((match := _POINT_B_RE.match(probe)) and "план" in probe[:40]):
            kind = "planned_b"
        elif match := _POINT_B_RE.match(probe):
            if "клуб" in probe:
                kind = "point_b_club"
            elif re.search(r"після|базов", probe):
                kind = "point_b_program"
            else:
                kind = "point_b"
        elif match := _POINT_A_RE.match(probe):
            kind = "point_a_club" if "клуб" in probe else "point_a"
        if kind is None or match is None:
            value = _money_value(text) if open_heading and len(text) <= 60 and has_currency(text) else None
            if value is not None:
                metrics.lines.append(MetricLine(open_heading, text, value, True, has_non_usd(text)))
            else:
                open_heading = None
            continue
        # Значення шукаємо після назви показника: «6. Точка Б …» — це номер пункту, а не сума
        value_text = text[match.end() :]
        if kind == "price":
            # «Ціна питання: (15 000 − 10 000) × 12 = 60 000 $» — беремо результат після «=»
            value_text = text.rsplit("=", 1)[1] if "=" in text else text
            if _money_value(value_text) is None and idx + 1 < len(texts):
                value_text = texts[idx + 1]
        metrics.lines.append(
            MetricLine(
                kind=kind,
                text=text,
                value=_money_value(value_text),
                has_currency=has_currency(value_text),
                non_usd=has_non_usd(value_text) and not re.search(r"\$|usd|дол", value_text, re.IGNORECASE),
            )
        )
        open_heading = kind if metrics.lines[-1].value is None and kind != "price" else None
    return metrics


def _check_currency(metrics: Metrics, issues: IssueCollector) -> None:
    without = [m.text for m in metrics.lines if m.value is not None and not m.has_currency]
    if without:
        issues.add("missing_currency", f"Рядки: {quote_list(without, 3)}")
    wrong = [m.text for m in metrics.lines if m.non_usd]
    if wrong:
        issues.add("wrong_currency", f"Показники мають бути в доларах: {quote_list(wrong, 3)}")


def _check_question_value(metrics: Metrics, settings: Settings, issues: IssueCollector) -> None:
    planned = [m for m in metrics.planned if m.value is not None]
    price = [m for m in metrics.of("price") if m.value is not None]
    current = metrics.current
    if not planned or not price or not current:
        missing = [
            title
            for title, items in (("Точка А / т.Б клубу", current), ("Планована т.Б", planned), ("Ціна питання", price))
            if not items
        ]
        if len(missing) < 3 and metrics.of("price"):
            issues.add(
                "wrong_question_value_calculation",
                "Неможливо перевірити розрахунок: не знайдено числового значення — " + ", ".join(f"«{m}»" for m in missing),
            )
        return

    multiplier = settings.question_value_multiplier
    target = planned[-1].value
    stated = price[-1].value
    tolerance = max(settings.question_value_tolerance, abs(stated) * 0.01)
    if any(abs((target - c.value) * multiplier - stated) <= tolerance for c in current):
        return
    base = current[-1].value
    expected = (target - base) * multiplier
    issues.add(
        "wrong_question_value_calculation",
        f"Вказано {format_number(stated)}, а за формулою ({format_number(target)} − {format_number(base)}) "
        f"× {multiplier:g} = {format_number(expected)}",
    )


def _check_club_metrics(metrics: Metrics, issues: IssueCollector) -> None:
    if not metrics.of("point_b_club", "point_a_club", "point_b_program"):
        issues.add("missing_point_b_club")
    if not metrics.growth_reason:
        issues.add("missing_growth_reason")


def check_hypothesis(doc: ParsedDocument, is_club: bool, settings: Settings, issues: IssueCollector) -> Metrics:
    body = doc.body_paragraphs
    metrics = extract_metrics(body)
    if not metrics.of("point_a", "point_a_club"):
        issues.add("missing_point_a")
    if not metrics.planned:
        issues.add("missing_point_b")
    if not metrics.of("price"):
        issues.add("missing_question_value")
    if is_club:
        _check_club_metrics(metrics, issues)

    rest = _probe(_text_without_metrics(body, metrics))
    if not _HYPOTHESIS_TEXT_RE.search(rest) and len(re.findall(r"[^\W\d_]{3,}", rest)) < _MIN_HYPOTHESIS_WORDS:
        issues.add("missing_hypothesis_description")
    if not _QUESTIONS_RE.search(rest):
        issues.add("missing_author_questions")

    _check_currency(metrics, issues)
    _check_question_value(metrics, settings, issues)
    return metrics


def check_strategic_question(
    doc: ParsedDocument, is_club: bool, settings: Settings, issues: IssueCollector
) -> Metrics:
    body = doc.body_paragraphs
    metrics = extract_metrics(body)
    if not metrics.of("point_a", "point_a_club"):
        issues.add("missing_point_a")
    if is_club:
        # Для річного доступу в стратегічному питанні обов'язкові ті самі показники, що й у гіпотезі
        if not metrics.planned:
            issues.add("missing_point_b")
        if not metrics.of("price"):
            issues.add("missing_question_value")
        _check_club_metrics(metrics, issues)

    text = _probe(doc.body_text)
    if not _STRATEGIC_REASON_RE.search(text):
        issues.add("missing_strategic_reason")
    if not _OPTIONS_RE.search(text):
        issues.add("missing_solution_options")

    _check_currency(metrics, issues)
    if metrics.of("price"):
        _check_question_value(metrics, settings, issues)
    return metrics


def check_dz(doc: ParsedDocument, dz_number: str | None, has_funnel: bool, links: list[str], issues: IssueCollector) -> None:
    if dz_number == "2":
        if not _dz_point_a_described(doc.body_paragraphs):
            issues.add("missing_point_a", "У ДЗ№2 потрібно описати точку А з сумою в $ (А = Кл × $)")
        if not has_funnel:
            issues.add("missing_funnel", "У ДЗ№2 воронку (п. 2 і п. 6) потрібно оформити пірамідою SmartArt або зображенням")
    elif dz_number == "7" and not links:
        issues.add("missing_landing_link")
    if not links:
        mention = _DRIVE_MENTION_RE.search(doc.body_text)
        if mention:
            issues.add("missing_material_link", f"У роботі згадано «{mention.group(0)}», але посилання немає")


# «Креатив скинув через google диск» — матеріали є, але посилання на них у роботі немає
_DRIVE_MENTION_RE = re.compile(r"(?:google|гугл)[\s-]*(?:диск\w*|drive)", re.IGNORECASE)


_POINT_A_ANYWHERE_RE = re.compile(r"точк\w*\s*[«\"']?а(?![^\W\d_])|т\.\s*а(?![^\W\d_])")


def _dz_point_a_described(lines: list[ParagraphInfo]) -> bool:
    """У ДЗ№2 точку А розписують по-різному (у тексті завдання, таблицею, формулою «8 000$ = 114 × 70$»):
    достатньо, щоб біля згадки точки А була сума."""
    texts = [normalize(p.text) for p in lines]
    for idx, text in enumerate(texts):
        m = _POINT_A_ANYWHERE_RE.search(_probe(text))
        if not m:
            continue
        window = [text[m.end() :], *texts[idx + 1 : idx + 4]]
        if any(_money_value(t) is not None and has_currency(t) for t in window):
            return True
    return False


def _text_without_metrics(body: list[ParagraphInfo], metrics: Metrics) -> str:
    metric_texts = {m.text for m in metrics.lines}
    return "\n".join(p.text for p in body if normalize(p.text) not in metric_texts)
