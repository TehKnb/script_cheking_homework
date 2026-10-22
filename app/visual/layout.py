"""Розбір воронки за геометрією: блоки (кроки) + текстові токени з координатами.

Спільне ядро для двох джерел:
* векторні воронки (SmartArt, фігури Word) — блоки і слова беруться з відрендереного PDF;
* воронки-зображення — блоки знаходить OpenCV, текст — OCR.

Правила оформлення (з методички та текстів відмов):
* у центрі (в блоках піраміди) — числові значення кроків або нічого (гіпотетична воронка);
* праворуч від кожного блоку — опис кроку;
* ліворуч між блоками — конверсія у %, = наступний / попередній × 100%;
  для першого переходу ліворуч може стояти частота показів без % — її не рахуємо.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.utils.numbers import find_numbers

_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_UNIT_WORDS_RE = re.compile(r"грн|uah|usd|шт|осіб|чол|тис|млн|[kк](?![^\W\d_])|\$", re.IGNORECASE)
_OCR_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "О": "0", "о": "0", "l": "1", "I": "1", "|": "1"})
_CID_RE = re.compile(r"\(cid:\d+\)")


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        return self.x1 - pad <= x <= self.x2 + pad and self.y1 - pad <= y <= self.y2 + pad


@dataclass(frozen=True)
class Token:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float = 1.0
    size: float = 0.0  # розмір шрифту (pt), якщо відомий

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def height(self) -> float:
        return self.y2 - self.y1


@dataclass
class TokenInfo:
    token: Token
    value: tuple[float, str] | None  # (значення, сирий текст)
    percent: float | None
    letters: int  # літер без одиниць виміру


@dataclass
class FunnelStage:
    box: Box
    value: float | None = None
    raw: str = ""
    label: str = ""
    inside_text: str = ""
    label_box: Box | None = None  # де стоїть опис (для перевірки, що описи рівно біля своїх блоків)

    @property
    def cy(self) -> float:
        return self.box.cy


@dataclass
class ConversionCheck:
    upper: FunnelStage
    lower: FunnelStage
    stated: float | None
    expected: float | None
    frequency: bool = False  # частота показів на першому кроці — не перевіряється
    stated_decimals: int = 0  # скільки знаків після коми написав студент («1,15%» → 2)
    stated_box: Box | None = None  # де стоїть вказана конверсія


@dataclass
class FunnelAnalysis:
    stages: list[FunnelStage] = field(default_factory=list)
    conversions: list[ConversionCheck] = field(default_factory=list)
    misplaced_conversions: list[str] = field(default_factory=list)  # % у піраміді або праворуч
    extra_labels: list[str] = field(default_factory=list)
    unformatted_numbers: list[str] = field(default_factory=list)
    side_values: list[float] = field(default_factory=list)  # числа ліворуч/праворуч (не %)
    overlapping_fields: list[str] = field(default_factory=list)  # описи/конверсії, чиї поля заходять на блоки
    source: str = "vector"  # vector (PDF, точні координати) | raster (OCR — читає не все)
    is_funnel: bool = False
    unreadable: bool = False
    blank: bool = False  # зображення: блоки воронки порожні (ні значень, ні тексту всередині)

    @property
    def has_values(self) -> bool:
        return sum(1 for s in self.stages if s.value is not None) >= 2

    @property
    def is_hypothetical(self) -> bool:
        return not any(s.value is not None for s in self.stages)

    @property
    def stated_conversions(self) -> list[ConversionCheck]:
        return [c for c in self.conversions if c.stated is not None]

    @property
    def readable_share(self) -> float:
        """Частка кроків, для яких прочитано значення або опис (для OCR — міра довіри до результату)."""
        if not self.stages:
            return 0.0
        return sum(1 for s in self.stages if s.value is not None or s.label) / len(self.stages)


# ------------------------------------------------------------------ tokens


def clean_text(text: str) -> str:
    return _CID_RE.sub("?", text).strip()


def _fix_digits(text: str) -> str:
    compact = re.sub(r"[\s.,%]", "", text)
    if not compact or not any(ch.isdigit() for ch in compact):
        return text
    fixed = compact.translate(_OCR_DIGIT_FIXES)
    if sum(ch.isdigit() for ch in fixed) / len(fixed) >= 0.6:
        return text.translate(_OCR_DIGIT_FIXES)
    return text


def classify_token(token: Token) -> TokenInfo:
    text = _fix_digits(clean_text(token.text))
    percent_match = _PERCENT_RE.search(text)
    percent = float(percent_match.group(1).replace(",", ".")) if percent_match else None

    remainder = _PERCENT_RE.sub(" ", text)
    letters = sum(ch.isalpha() for ch in _UNIT_WORDS_RE.sub("", remainder))
    value: tuple[float, str] | None = None
    numbers = find_numbers(remainder)
    if numbers:
        best = max(numbers, key=lambda n: n.value)
        digits = sum(ch.isdigit() for ch in best.raw)
        # «Етап 1» — це назва, а не значення; «10 000 переглядів» — значення
        if letters == 0 or digits >= 3:
            value = (best.value, best.raw)
    return TokenInfo(token=token, value=value, percent=percent, letters=letters)


def group_rows(items: list[TokenInfo]) -> list[list[TokenInfo]]:
    rows: list[list[TokenInfo]] = []
    for item in sorted(items, key=lambda i: i.token.cy):
        t = item.token
        for row in rows:
            r_top = min(i.token.y1 for i in row)
            r_bottom = max(i.token.y2 for i in row)
            overlap = min(r_bottom, t.y2) - max(r_top, t.y1)
            if overlap >= 0.5 * min(t.height, r_bottom - r_top):
                row.append(item)
                break
        else:
            rows.append([item])
    return rows


def row_text(row: list[TokenInfo]) -> str:
    return " ".join(clean_text(i.token.text) for i in sorted(row, key=lambda i: i.token.x1))


def row_value(row: list[TokenInfo]) -> tuple[float, str] | None:
    """Значення рядка: токени склеюються («10» + «000» -> «10 000»)."""
    text = " ".join(i.token.text for i in sorted(row, key=lambda i: i.token.x1) if i.percent is None)
    joined = classify_token(Token(text, 0, 0, 0, 0)).value
    if joined:
        return joined
    values = [i.value for i in row if i.value]
    return max(values, key=lambda v: v[0]) if values else None


# ------------------------------------------------------------------- chains


def find_chains(boxes: list[Box], min_len: int = 3) -> list[list[Box]]:
    """Ланцюжки блоків «один під одним»: вирівняні по центру, без великих проміжків,
    ширина монотонно звужується або розширюється (воронка / піраміда SmartArt).
    Оргструктури (однакові прямокутники, кілька в ряд) сюди не потрапляють."""
    ordered = _dedupe(sorted(boxes, key=lambda b: (b.y1, b.x1)))
    taken: set[int] = set()
    chains: list[list[Box]] = []
    for start in range(len(ordered)):
        if start in taken:
            continue
        chain_idx = [start]
        while True:
            last = ordered[chain_idx[-1]]
            candidates = [
                j
                for j, c in enumerate(ordered)
                if j not in taken
                and j not in chain_idx
                and c.y1 >= last.y1 + 0.5 * last.height
                and c.y1 - last.y2 <= max(0.8 * last.height, 12)
                and abs(c.cx - last.cx) <= 0.12 * max(c.width, last.width) + 3
            ]
            if not candidates:
                break
            chain_idx.append(min(candidates, key=lambda j: ordered[j].y1))
        chain = [ordered[j] for j in chain_idx]
        if len(chain) >= min_len and _is_tapered(chain):
            chains.append(chain)
            taken.update(chain_idx)
    return chains


def _is_tapered(chain: list[Box]) -> bool:
    widths = [b.width for b in chain]
    narrowing = all(widths[k] >= widths[k + 1] * 0.95 for k in range(len(widths) - 1))
    widening = all(widths[k] <= widths[k + 1] * 1.05 for k in range(len(widths) - 1))
    # Піраміда SmartArt звужується помітно (у 2–6 разів); оргструктура — прямокутники майже однакові
    return (narrowing or widening) and abs(widths[0] - widths[-1]) >= 0.25 * max(widths)


def _dedupe(boxes: list[Box]) -> list[Box]:
    result: list[Box] = []
    for b in boxes:
        if not any(abs(b.x1 - r.x1) < 2 and abs(b.y1 - r.y1) < 2 and abs(b.x2 - r.x2) < 2 and abs(b.y2 - r.y2) < 2 for r in result):
            result.append(b)
    return result


# ---------------------------------------------------------------- analysis


def analyze_stages(
    blocks: list[Box],
    infos: list[TokenInfo],
    side_limit: float | None = None,
) -> FunnelAnalysis:
    """Аналіз воронки з відомими блоками (кроками), відсортованими згори донизу.

    side_limit — максимальна відстань від краю блоку до підпису/конверсії: відсікає основний текст
    документа, яким Word обтікає воронку.
    """
    stages = [FunnelStage(box=b) for b in sorted(blocks, key=lambda b: b.y1)]
    analysis = FunnelAnalysis(stages=stages, is_funnel=True)
    if not stages:
        return analysis

    bands = _bands(stages)
    used: set[int] = set()

    # 1. Вміст блоків: кожен токен належить одному блоку — найближчому по вертикалі з тих, що його містять
    owner: dict[int, int] = {}
    for k, info in enumerate(infos):
        containing = [
            idx for idx, s in enumerate(stages) if s.box.contains(info.token.cx, info.token.cy, pad=1.0)
        ]
        if containing:
            owner[k] = min(containing, key=lambda idx: abs(stages[idx].box.cy - info.token.cy))
    used.update(owner)
    for idx, stage in enumerate(stages):
        items = [infos[k] for k, o in owner.items() if o == idx]
        numeric = [i for i in items if i.percent is None and i.value is not None]
        if numeric:
            value = _stacked_value(numeric) or row_value(numeric)
            if value:
                stage.value, stage.raw = value
        stage.inside_text = row_text([i for i in items if i.letters >= 2])
        analysis.misplaced_conversions += [clean_text(i.token.text) for i in items if i.percent is not None]

    # 2. Ліворуч / праворуч від блоків: текст ділимо на кластери (фрагменти рядка між великими проміжками).
    #    Рядок основного тексту, що перетинає воронку, — не підпис і не конверсія.
    rest = [info for k, info in enumerate(infos) if k not in used]
    heights = sorted(i.token.height for i in rest) or [10.0]
    gap = 12.0 if side_limit is not None else 2.5 * heights[len(heights) // 2]
    left: list[TokenInfo] = []
    right_edge = max(s.box.x2 for s in stages)
    first, last = stages[0].box, stages[-1].box
    for cluster in _clusters(rest, gap):
        cx1 = min(i.token.x1 for i in cluster)
        cx2 = max(i.token.x2 for i in cluster)
        cy = sum(i.token.cy for i in cluster) / len(cluster)
        letters = sum(i.letters for i in cluster)
        idx = _band_index(bands, cy)
        if idx is None:
            near = side_limit is None or cx1 - right_edge <= side_limit
            in_range = first.y1 - 1.5 * first.height <= cy <= last.y2 + 1.5 * last.height
            if letters >= 2 and cx1 >= right_edge - 2 and near and in_range:
                analysis.extra_labels.append(row_text(cluster))
            continue
        box = stages[idx].box
        # Опис під вершиною (над верхнім блоком), що стоїть правіше за центр, — опис кроку, для якого немає блоку
        outside = (idx == len(stages) - 1 and cy > box.y2) or (idx == 0 and cy < box.y1)
        if outside and letters >= 2 and box.cx <= cx1 < box.x2 - 2:
            analysis.extra_labels.append(row_text(cluster))
            continue
        if cx1 >= box.x2 - 2 and (side_limit is None or cx1 - box.x2 <= side_limit):
            if letters >= 2:
                stage = stages[idx]
                stage.label = (stage.label + " " + row_text([i for i in cluster if i.percent is None])).strip()
                stage.label_box = _union([stage.label_box, *(Box(i.token.x1, i.token.y1, i.token.x2, i.token.y2) for i in cluster)])
            analysis.misplaced_conversions += [clean_text(i.token.text) for i in cluster if i.percent is not None]
            analysis.side_values += [i.value[0] for i in cluster if i.value and i.percent is None]
        elif cx2 <= box.x1 + 2 and (side_limit is None or box.x1 - cx2 <= side_limit):
            left += cluster
            analysis.side_values += [i.value[0] for i in cluster if i.value and i.percent is None]

    analysis.conversions = _match_conversions(stages, left)
    analysis.unformatted_numbers = [
        s.raw for s in stages if s.raw and re.fullmatch(r"\d{4,8}", s.raw.strip())
    ]
    return analysis


def _union(boxes: list[Box | None]) -> Box | None:
    real = [b for b in boxes if b is not None]
    if not real:
        return None
    return Box(min(b.x1 for b in real), min(b.y1 for b in real), max(b.x2 for b in real), max(b.y2 for b in real))


def _stacked_value(numeric: list[TokenInfo]) -> tuple[float, str] | None:
    """У вузькому нижньому блоці число переноситься по цифрах («3» над «0» = 30)."""
    rows = group_rows(numeric)
    if len(rows) < 2 or not all(len(r) == 1 and r[0].token.text.strip().isdigit() for r in rows):
        return None
    digits = "".join(r[0].token.text.strip() for r in rows)
    return float(digits), digits


def _clusters(items: list[TokenInfo], gap: float) -> list[list[TokenInfo]]:
    clusters: list[list[TokenInfo]] = []
    for row in group_rows(items):
        row = sorted(row, key=lambda i: i.token.x1)
        current = [row[0]]
        for item in row[1:]:
            if item.token.x1 - current[-1].token.x2 > gap:
                clusters.append(current)
                current = [item]
            else:
                current.append(item)
        clusters.append(current)
    return clusters


def _bands(stages: list[FunnelStage]) -> list[tuple[float, float]]:
    bands: list[tuple[float, float]] = []
    for idx, stage in enumerate(stages):
        top = (stages[idx - 1].box.y2 + stage.box.y1) / 2 if idx > 0 else stage.box.y1 - stage.box.height * 0.75
        bottom = (
            (stage.box.y2 + stages[idx + 1].box.y1) / 2 if idx + 1 < len(stages) else stage.box.y2 + stage.box.height * 0.75
        )
        bands.append((top, bottom))
    return bands


def _band_index(bands: list[tuple[float, float]], y: float) -> int | None:
    for idx, (top, bottom) in enumerate(bands):
        if top <= y <= bottom:
            return idx
    return None


def _match_conversions(stages: list[FunnelStage], left: list[TokenInfo]) -> list[ConversionCheck]:
    if len(stages) < 2:
        return []
    percents = sorted((i for i in left if i.percent is not None), key=lambda i: i.token.cy)
    # «100%» біля першого блоку — не конверсія між блоками
    percents = [p for p in percents if not (abs(p.percent - 100) < 1e-9 and p.token.cy <= stages[0].box.y2)]
    transitions = list(zip(stages, stages[1:]))

    stated: list[TokenInfo | None] = [None] * len(transitions)
    # Кожен відсоток — до найближчої межі між блоками (а не першому переходу, у вікно якого він потрапив)
    pairs = sorted(
        (abs(p.token.cy - (upper.box.y2 + lower.box.y1) / 2), t_idx, c_idx)
        for t_idx, (upper, lower) in enumerate(transitions)
        for c_idx, p in enumerate(percents)
        if upper.box.y1 <= p.token.cy <= lower.box.y2
    )
    used: set[int] = set()
    for _, t_idx, c_idx in pairs:
        if stated[t_idx] is None and c_idx not in used:
            used.add(c_idx)
            stated[t_idx] = percents[c_idx]
    # Порядок збігся, але геометрія неточна (OCR) — розкладаємо за порядком
    if len(percents) == len(transitions) and any(s is None for s in stated):
        stated = list(percents)

    # Перший перехід (охоплення → покази): частоту показів пишуть без % і за формулою не рахують (методичка, п. 10).
    # Якщо ж стоїть «%», це конверсія — її перевіряємо (частоту, записану з «%», приймає is_conversion_correct)
    first_frequency = stated[0] is None and (
        any(
            i.percent is None and i.value is not None and stages[0].box.y1 <= i.token.cy <= stages[1].box.y2
            for i in left
        )
        or (stages[0].value is not None and stages[1].value is not None and stages[1].value > stages[0].value)
    )

    checks: list[ConversionCheck] = []
    for t_idx, ((upper, lower), info) in enumerate(zip(transitions, stated)):
        expected = (
            lower.value / upper.value * 100
            if upper.value is not None and lower.value is not None and upper.value
            else None
        )
        checks.append(
            ConversionCheck(
                upper,
                lower,
                info.percent if info else None,
                expected,
                frequency=t_idx == 0 and first_frequency,
                stated_decimals=_percent_decimals(info.token.text) if info else 0,
                stated_box=Box(info.token.x1, info.token.y1, info.token.x2, info.token.y2) if info else None,
            )
        )
    return checks


def _percent_decimals(text: str) -> int:
    m = _PERCENT_RE.search(_fix_digits(clean_text(text)))
    if not m or not re.search(r"[.,]", m.group(1)):
        return 0
    return len(re.split(r"[.,]", m.group(1))[1])


def is_conversion_correct(check: ConversionCheck, tolerance_pp: float = 0.0) -> bool:
    """Похибка — менше одиниці останнього знака, який написав студент: «20%» для 19,6%, «28%» для 28,6%,
    «72%» для 71,43% (округлення вгору в прийнятих роботах трапляється), «7,6%» для 7,64%, «1,15%» для 1,159%.
    А от «8,5%» для 7,64% чи «1,9%» для 1,16% — помилка. Для великих значень (покази/охоплення > 100%)
    допускаємо ще 1% відносної похибки; tolerance_pp — додатковий допуск у п.п. (за замовчуванням 0)."""
    if check.frequency:
        return True
    if check.stated is None or check.expected is None:
        return False
    unit = 10.0**-check.stated_decimals
    diff = abs(check.stated - check.expected)
    if diff < unit - 1e-9 or diff <= tolerance_pp:
        return True
    if abs(check.expected) < 100:
        return False
    # «Покази > охоплення»: 240,9% або та сама частота показів, записана з «%» («2,4%»)
    return diff <= 0.01 * abs(check.expected) or abs(check.stated - check.expected / 100) < unit - 1e-9
