"""Перевірка арифметики в тексті роботи: «А = 693 × 3,90 = 2 700 $», «ROI = (8 000 − 500) ÷ 500 × 100% = 1 500%»,
«8 556 + 1 000 + 267 = 9 823», ланцюжки «70*30000=2100000/8=262500», запис «8 000$ = 114 * 70$».

Студенти округлюють і множники («0,3 × 49 000 = 16 333», де 0,3 — це 1/3), і результати («= 66,7%», «= −43%»),
тому кожне число має точність свого запису (0,3 → 0,25…0,35), а результат може бути округлений чи обрізаний
до останнього знака (і ще 1% похибки). Помилка — лише якщо вказаний результат поза цими межами.

Окремо — розкладання точки А на частини: «А1 = …», «А2 = …» мають у сумі давати точку А.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.issues import IssueCollector, as_list
from app.services.document_loader import ParsedDocument
from app.utils.numbers import format_number
from app.utils.text import shorten

RELATIVE_TOLERANCE = 0.01
# Розкладання точки А: сума частин може відрізнятися від загальної суми через округлення
PARTS_TOLERANCE = 0.05

# Число не може бути частиною позначення («А1», «S2», «АIV»): перед ним не буква.
# Тисячі — через пробіл/апостроф групами по 3 цифри («1 208 741», «2’554»), дробова частина — через кому чи крапку
_NUMBER = r"(?<![^\W\d_])\d{1,3}(?:[   ’'`´](?:\d{3}))+(?:[.,]\d+)?(?!\d)|(?<![^\W\d_])\d+(?:[.,]\d+)?"
_TOKEN_RE = re.compile(
    rf"(?P<num>{_NUMBER})|(?P<op>[+\-−–×*/÷:]|(?<![^\W\d_])[xхXХ](?![^\W\d_]))|(?P<lpar>\()|(?P<rpar>\))|(?P<eq>=|≈)|(?P<pct>%)"
    r"|(?P<skip>\$|€|zł|₴|грн\.?|у\.\s?о\.?|usd|uah|eur|дол(?:ар\w*|\.)?|\s+)"
    r"|(?P<brk>.)",
    re.IGNORECASE,
)
_REMARK_RE = re.compile(r"\((?=[^()]*[^\W\d_])[^()\d]*\)")
# x/х множенням є лише між числами («14 х 100 %»), а не в словах — слова розривають вираз раніше
_MUL = {"×", "*", "x", "х", "X", "Х"}
_DIV = {"/", "÷", ":"}
_MINUS = {"-", "−", "–"}


@dataclass(frozen=True)
class Tok:
    kind: str  # num | op | lpar | rpar | eq | pct
    text: str
    value: float = 0.0
    unit: float = 0.0  # одиниця останнього знака числа: «0,3» → 0,1; «2 700» → 1


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float

    def __add__(self, other: Interval) -> Interval:
        return Interval(self.lo + other.lo, self.hi + other.hi)

    def __sub__(self, other: Interval) -> Interval:
        return Interval(self.lo - other.hi, self.hi - other.lo)

    def __mul__(self, other: Interval) -> Interval:
        products = [self.lo * other.lo, self.lo * other.hi, self.hi * other.lo, self.hi * other.hi]
        return Interval(min(products), max(products))

    def __truediv__(self, other: Interval) -> Interval:
        if other.lo <= 0 <= other.hi:
            raise ZeroDivisionError
        return self * Interval(min(1 / other.lo, 1 / other.hi), max(1 / other.lo, 1 / other.hi))

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2


@dataclass
class Segment:
    """Частина виразу між знаками «=»."""

    tokens: list[Tok]
    value: Interval
    is_number: bool  # одне число без дій
    has_percent_scale: bool  # містить «× 100%» / «× 100» — результат уже у відсотках
    percent_result: bool  # саме число з «%» («= 17%»)

    @property
    def text(self) -> str:
        return _display(self.tokens)

    @property
    def first_number(self) -> Tok | None:
        return next((t for t in self.tokens if t.kind == "num"), None)


class _ParseError(Exception):
    pass


def check_calculations(doc: ParsedDocument, issues: IssueCollector) -> None:
    errors: list[str] = []
    for paragraph in doc.body_paragraphs:
        # Ціну питання перевіряє окреме правило Гіпотези — не дублюємо
        if re.search(r"ціна\s+питання", paragraph.text, re.IGNORECASE):
            continue
        errors += find_calculation_errors(paragraph.text)
    if errors:
        issues.add("wrong_calculation", _bullets(errors, 6))

    mismatches = find_point_a_parts_mismatches([p.text for p in doc.body_paragraphs])
    if mismatches:
        issues.add("point_a_parts_mismatch", _bullets(mismatches, 3))


def _bullets(items: list[str], limit: int) -> str:
    return as_list(items, limit)


def find_calculation_errors(line: str) -> list[str]:
    errors: list[str] = []
    for span in _spans(_tokenize(line)):
        segments = _segments(span)
        if segments is None:
            continue
        for left, right in zip(segments, segments[1:]):
            error = _compare(left, right)
            if error:
                errors.append(error)  # у ланцюжку перевіряємо кожен крок від того, що написав студент
    return errors


# ---------------------------------------------------------------- tokenizing


def _tokenize(line: str) -> list[Tok | None]:
    """Токени виразів; None — розрив (слово, кома-розділювач тощо)."""
    # Примітки в дужках без чисел — «( 9 $ − 4 $ (собівартість) ) / 1,27 $» — не розривають вираз
    line = _REMARK_RE.sub(" ", line)
    tokens: list[Tok | None] = []
    for m in _TOKEN_RE.finditer(line):
        kind = m.lastgroup
        text = m.group(0)
        if kind == "skip":
            continue
        if kind == "num":
            tokens.append(_number(text))
        elif kind == "brk":
            tokens.append(None)
        else:
            tokens.append(Tok(kind, text))
    return tokens


def _number(text: str) -> Tok:
    compact = re.sub(r"[   ’'`´]", "", text)
    decimals = 0
    if re.search(r"[.,]\d+$", compact):
        int_part, frac = re.split(r"[.,]", compact, maxsplit=1)
        decimals = len(frac)
        value = float(f"{int_part}.{frac}")
    else:
        value = float(compact)
    return Tok("num", text.strip(), value, 10.0**-decimals)


def _spans(tokens: list[Tok | None]) -> list[list[Tok]]:
    """Неперервні ділянки без слів, у яких є і «=», і хоча б одна арифметична дія між числами."""
    spans: list[list[Tok]] = []
    current: list[Tok] = []
    for tok in [*tokens, None]:
        if tok is not None:
            current.append(tok)
            continue
        if any(t.kind == "eq" for t in current) and any(t.kind == "op" for t in current):
            spans.append(current)
        current = []
    return spans


def _segments(span: list[Tok]) -> list[Segment] | None:
    parts: list[list[Tok]] = [[]]
    for tok in span:
        if tok.kind == "eq":
            parts.append([])
        else:
            parts[-1].append(tok)
    # «ROI = …»: до першого «=» могло стояти лише слово — порожній початок відкидаємо
    while parts and not parts[0]:
        parts.pop(0)
    if len(parts) < 2 or any(not p for p in parts):
        return None
    segments: list[Segment] = []
    for idx, part in enumerate(parts):
        # Крайні частини обрізаємо до найдовшого коректного виразу: «… = 148 000 (27,9%)»
        if idx == 0 and part[0].kind == "op" and part[0].text in _MINUS:
            part = part[1:]  # «Точка А – 3 000 = …»: тире після назви — роздільник, а не мінус
            if not part:
                return None
        segment = _parse_trimmed(part, from_end=idx == 0, from_start=idx == len(parts) - 1)
        if segment is None:
            return None
        segments.append(segment)
    if not any(not s.is_number for s in segments):
        return None
    return segments


def _parse_trimmed(part: list[Tok], from_end: bool, from_start: bool) -> Segment | None:
    candidates = [part]
    # Відкидати можна лише «хвости», що не є частиною виразу: зліва — знаки без чисел і без «)»
    # («ROI: 10 / 2 = 5»), справа — примітку в дужках («= 148 000 (27,9%)»). Інакше вираз обрізався б посередині
    if from_end:
        candidates += [
            part[i:] for i in range(1, len(part)) if all(t.kind in ("op", "lpar", "pct") for t in part[:i])
        ]
    if from_start:
        candidates += [
            part[:i] for i in range(len(part) - 1, 0, -1)
            if part[i].kind == "lpar" or all(t.kind != "num" for t in part[i:])
        ]
    for tokens in candidates:
        try:
            return _parse(tokens)
        except (_ParseError, ZeroDivisionError):
            continue
    return None


# ------------------------------------------------------------------- parsing


def _parse(tokens: list[Tok]) -> Segment:
    parser = _Parser(tokens)
    value = parser.expression()
    if parser.pos != len(tokens):
        raise _ParseError
    nums = [t for t in tokens if t.kind == "num"]
    if not nums:
        raise _ParseError
    body = tokens[1:] if tokens[0].kind == "op" and tokens[0].text in _MINUS else tokens  # «= −18,59%»
    is_number = len(nums) == 1 and all(t.kind in ("num", "pct") for t in body) and body[0].kind == "num"
    return Segment(
        tokens=tokens,
        value=value,
        is_number=is_number,
        has_percent_scale=parser.percent_scale,
        percent_result=is_number and tokens[-1].kind == "pct",
    )


class _Parser:
    """expr := term (('+'|'-') term)*; term := factor (('*'|'/') factor)*; factor := '-'? (num '%'? | '(' expr ')')"""

    def __init__(self, tokens: list[Tok]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.percent_scale = False

    def peek(self) -> Tok | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> Tok:
        tok = self.peek()
        if tok is None:
            raise _ParseError
        self.pos += 1
        return tok

    def expression(self) -> Interval:
        value = self.term()
        while (tok := self.peek()) is not None and tok.kind == "op" and (tok.text == "+" or tok.text in _MINUS):
            self.take()
            right = self.term()
            value = value + right if tok.text == "+" else value - right
        return value

    def term(self) -> Interval:
        value = self.factor()
        while (tok := self.peek()) is not None and tok.kind == "op" and (tok.text in _MUL or tok.text in _DIV):
            self.take()
            right = self.factor()
            value = value * right if tok.text in _MUL else value / right
        return value

    def factor(self) -> Interval:
        tok = self.take()
        if tok.kind == "op" and tok.text in _MINUS:
            inner = self.factor()
            return Interval(-inner.hi, -inner.lo)
        if tok.kind == "lpar":
            value = self.expression()
            if self.take().kind != "rpar":
                raise _ParseError
            return value
        if tok.kind != "num":
            raise _ParseError
        half = tok.unit / 2 if tok.unit < 1 else 0.0  # цілі множники точні, дробові — з точністю запису
        value = Interval(tok.value - half, tok.value + half)
        if (nxt := self.peek()) is not None and nxt.kind == "pct":
            self.take()
            if tok.value == 100:
                self.percent_scale = True  # «× 100%» — переведення у відсотки
            else:
                value = value * Interval(0.01, 0.01)
        elif tok.value == 100 and self.pos >= 2 and self.tokens[self.pos - 2].text in _MUL:
            self.percent_scale = True
        return value


# ---------------------------------------------------------------- comparison


def _matches(computed: Interval, stated: Tok, scale: float = 1.0) -> bool:
    target = stated.value
    tol = max(stated.unit, RELATIVE_TOLERANCE * abs(target))
    return computed.lo * scale - tol <= target <= computed.hi * scale + tol


def _stated(segment: Segment) -> Tok | None:
    tok = segment.first_number
    if tok is None:
        return None
    if segment.tokens[0].kind == "op" and segment.tokens[0].text in _MINUS:  # «= −18,59%»
        return Tok("num", "−" + tok.text, -tok.value, tok.unit)
    return tok


def _segment_matches(computed: Segment, stated_segment: Segment) -> bool:
    stated = _stated(stated_segment)
    if stated is None:
        return True
    if _matches(computed.value, stated):
        return True
    # «(1 898 262 − 1 620 000) / 1 620 000 = 17%» — частка, записана у відсотках
    return stated_segment.percent_result and not computed.has_percent_scale and _matches(computed.value, stated, 100)


def _compare(left: Segment, right: Segment) -> str | None:
    if right.is_number:
        if _segment_matches(left, right):
            return None
        return _describe(left, right)
    if left.is_number:  # «8 000$ = 114 * 70$»
        if _segment_matches(right, left):
            return None
        return _describe(right, left)
    # Обидві частини — вирази: «70*30000 = 2100000/8» — ланцюжок, де права частина продовжує обчислення
    first = right.first_number
    if first is not None and _matches(left.value, first):
        return None
    if abs(left.value.mid - right.value.mid) <= max(RELATIVE_TOLERANCE * abs(right.value.mid), 1e-9):
        return None
    shown = Segment([first], Interval(first.value, first.value), True, False, False) if first else right
    return _describe(left, shown)


def _describe(expression: Segment, stated: Segment) -> str:
    """«70 × 30 000 = 2 250 000 → правильно: 2 100 000». Знаки — × і ÷ (а не * і /, які Telegram-розмітка
    сприймає як форматування), числа — з пробілами між тисячами."""
    value = expression.value.mid
    percent = stated.percent_result
    if percent and not expression.has_percent_scale:
        value *= 100
    expected = format_number(round(value, 2)).replace("-", "−", 1) + ("%" if percent else "")
    return f"{shorten(_display(expression.tokens), 70)} = {_display(stated.tokens)} → правильно: {expected}"


_DISPLAY_OPS = {**{op: "×" for op in _MUL}, **{op: "÷" for op in _DIV}, **{op: "−" for op in _MINUS}, "+": "+"}


def _display(tokens: list[Tok]) -> str:
    parts: list[str] = []
    for i, tok in enumerate(tokens):
        if tok.kind == "num":
            text = format_number(tok.value)
        elif tok.kind == "op":
            text = _DISPLAY_OPS.get(tok.text, tok.text)
        else:
            text = tok.text
        prev = tokens[i - 1] if i else None
        # Унарний мінус («−18,59%», «(−208)») пишемо впритул до числа
        after_unary = (
            prev is not None and prev.kind == "op" and prev.text in _MINUS and (i == 1 or tokens[i - 2].kind in ("op", "lpar"))
        )
        glue = not parts or tok.kind in ("pct", "rpar") or (prev is not None and prev.kind == "lpar") or after_unary
        parts.append(text if glue else " " + text)
    return "".join(parts)


# ---------------------------------------------------------- точка А на частини

# «А1 = 20*104$=2080$», «Точка А 2 (роздріб) – -1 600$», «АII =50*25700=1285000»
_PART_RE = re.compile(
    r"^\s*(?:[^\W\d_]+\s+){0,3}?(?:точк\w*\s+|т\.\s*)?[АA]\s*(?P<idx>\d{1,2}|[IVX]{1,4}|[ІV]{1,4})(?![\w.])[^=–\-:]*[=–\-:]",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?(?:моя\s+|наша\s+)?(?:точк\w*\s+|т\.\s*)?[АA](?![\w\d])(?:\([^)]*\)|[^=–\-:\d(])*[=–\-:]",
    re.IGNORECASE,
)
_ROMAN = {"I": 1, "V": 5, "X": 10, "І": 1}


def _index(raw: str) -> int:
    raw = raw.upper()
    if raw.isdigit():
        return int(raw)
    total = 0
    for i, ch in enumerate(raw):
        value = _ROMAN.get(ch, 0)
        total += -value if i + 1 < len(raw) and _ROMAN.get(raw[i + 1], 0) > value else value
    return total


def _final_value(text: str) -> float | None:
    """Підсумкове значення рядка: останнє число після останнього «=» (або після «–»/«:»)."""
    text = re.sub(r"\([^)]*\)", " ", text)  # «(потік 63)», «(після 7-ми тижневої програми)»
    tail = text.split("=")[-1] if "=" in text else re.split(r"[–\-:]", text, maxsplit=1)[-1]
    nums = [t for t in _tokenize(tail) if t is not None and t.kind == "num"]
    if not nums:
        return None
    # «- -1 600$», «– мінус 2 000$»: мінус, прилиплий до числа після роздільника. «мереж- -  2 000» — роздільники
    sign = -1 if re.search(r"(?:^|[-−–—:=]\s*)[-−]\d|мінус", tail.strip()) else 1
    return sign * nums[-1].value


def find_point_a_parts_mismatches(lines: list[str]) -> list[str]:
    """Групи «А1, А2, …» (по порядку, з 1) після рядка із загальною точкою А: сума частин = точка А."""
    found: list[str] = []
    total: float | None = None
    group: list[tuple[str, float]] = []

    def close() -> None:
        if total is not None and len(group) >= 2:
            parts_sum = sum(v for _, v in group)
            if abs(parts_sum - total) > max(PARTS_TOLERANCE * abs(total), 1):
                names = " + ".join(name for name, _ in group)
                values = " + ".join(format_number(round(v, 2)) for _, v in group).replace("+ -", "− ")
                found.append(
                    f"{names} = {values} = {format_number(round(parts_sum, 2))}, а точка А = {format_number(total)}"
                )

    for line in lines:
        part = _PART_RE.match(line)
        if part:
            idx = _index(part.group("idx"))
            value = _final_value(line[part.end() - 1 :])
            if idx == 1:
                close()
                group = []
            if value is None or idx != len(group) + 1:
                group = []
                continue
            group.append((f"А{part.group('idx')}", value))
            continue
        if _TOTAL_RE.match(line):
            value = _final_value(line)
            if value is not None:
                close()
                group = []
                total = value
    close()
    return found
