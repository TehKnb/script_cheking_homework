"""Розбір і перевірка шапки роботи (перший рядок документа та назва файлу).

Формат (з реальних прийнятих робіт):
    ДЗ:                   Прізвище Ім'я – Бізнес – Місто – Пакет+потік – ДЗ №N
    Гіпотеза:             Прізвище Ім'я – Бізнес – Місто (населення) – Пакет+потік – Гіпотеза
    Стратегічне питання:  Прізвище Ім'я – Бізнес – Місто (населення) – Пакет+потік – Стратегічне питання
Пакет пишеться разом із номером потоку: «ВІП67», «ВІП 67+», «ВІП+ 66», «Стандарт67», «Річна63», «КМ+ 49».
Для гіпотез і стратегічних питань з невеликих міст вказується населення: «м. Березівка (7 000), Одеська обл.».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import Settings
from app.issues import IssueCollector
from app.models import Area, WorkType
from app.utils.text import normalize, quote_list

SEPARATOR_RE = re.compile(r"\s*[–—]\s*|\s+-\s+|\s-(?=\S)|(?<=\S)-\s")
# Запасний варіант: «Коваль Ірина-Штори-карнизи-Черкаси-Річна63-Гіпотеза» (дефіси без пробілів)
_LOOSE_SEPARATOR_RE = re.compile(r"\s*[-–—]\s*")
_UNDERSCORE_RE = re.compile(r"\s*_\s*")

_DZ_MARKER_RE = re.compile(
    r"^(?:ДЗ|DZ|Д/З|домашн\w*\s+завдан\w*)(?![^\W\d_])\s*(?:№|#|N|No\.?)?\s*(?P<num>\d+)?",
    re.IGNORECASE,
)
_HYPOTHESIS_RE = re.compile(r"гіпотез|гипотез", re.IGNORECASE)
_STRATEGIC_RE = re.compile(
    r"стратегічн\w*\s+(?:питанн|запитанн)|стратегическ\w*\s+вопрос|важлив\w*\s+питанн|страт\.\s*питанн",
    re.IGNORECASE,
)
_DZ_ANYWHERE_RE = re.compile(r"(?<![^\W\d_])(?:ДЗ|DZ|Д/З)(?![^\W\d_])|домашн\w*\s+завдан", re.IGNORECASE)


# ------------------------------------------------------------------ packages


@dataclass(frozen=True)
class PackageInfo:
    canonical: str  # Стандарт | ВІП | ВІП+ | Річна | КМ+
    flow: str | None
    club: bool  # річний доступ / клуб: ВІП+, Річна, КМ+, будь-який пакет з «+»

    @property
    def display(self) -> str:
        return f"{self.canonical}{self.flow or ''}"


# Кольори, якими може бути виділений пакет; None — не перевіряється.
# «Річну» студенти зазвичай виділяють червоним, і це приймають так само, як чорний
PACKAGE_COLORS: dict[str, frozenset[str] | None] = {
    "Стандарт": frozenset({"blue"}),
    "ВІП": frozenset({"red"}),
    "ВІП+": frozenset({"red"}),
    "Річна": frozenset({"black", "red"}),
    "КМ+": None,
}

_PACKAGE_RE = re.compile(
    r"^(?P<name>віп|vip|вип|вiп|стандарт|standard|standart|річн[^\W\d_]*|рiчн[^\W\d_]*|годов[^\W\d_]*|км)"
    r"(?P<vip>\s*(?:віп|vip))?"
    r"\s*(?P<plus1>\+)?\s*[-–]?\s*(?P<flow>\d{1,3})?\s*(?P<plus2>\+)?$",
    re.IGNORECASE,
)


def parse_package(text: str) -> PackageInfo | None:
    cleaned = _strip_placeholders(text, ("пакет", "потік")).strip(" .,:;«»\"'")
    m = _PACKAGE_RE.match(cleaned)
    if not m:
        return None
    name = m.group("name").lower()
    plus = bool(m.group("plus1") or m.group("plus2"))
    if name.startswith(("віп", "vip", "вип", "вiп")):
        canonical = "ВІП+" if plus else "ВІП"
    elif m.group("vip"):  # «Річна ВІП 58» — річний ВІП
        canonical = "ВІП+"
    elif name.startswith(("стандарт", "standard", "standart")):
        canonical = "Стандарт"
    elif name == "км":
        canonical = "КМ+"
    else:
        canonical = "Річна"
    flow = m.group("flow")
    return PackageInfo(canonical=canonical, flow=flow, club=plus or canonical in ("ВІП+", "Річна", "КМ+"))


# --------------------------------------------------------------- placeholders

# Слова-замінники, яких не має бути в шапці («м.» допускається — так пишуть і в прийнятих роботах)
_PLACEHOLDER_PATTERNS: dict[str, re.Pattern[str]] = {
    "пакет": re.compile(r"(?<![^\W\d_])пакет\w*", re.IGNORECASE),
    "місто": re.compile(r"(?<![^\W\d_])(?:місто|город)(?![^\W\d_])", re.IGNORECASE),
    "потік": re.compile(r"(?<![^\W\d_])(?:потік|потоку|поток)(?![^\W\d_])", re.IGNORECASE),
}
_CITY_PREFIX_RE = re.compile(r"^(?:м|г|смт|с)\s*\.\s*|^(?:місто|город|смт)\s+", re.IGNORECASE)

_TEMPLATE_LITERALS = {
    "прізвище ім'я", "прізвище", "ім'я", "бізнес", "місто", "пакет", "ваш пакет", "потік", "назва бізнесу",
}
_FLOW_RE = re.compile(r"^(?:\d{1,3}\s*(?:потік|поток)?|(?:потік|поток)\s*\d{1,3})\+?$", re.IGNORECASE)
_NAME_WORD_RE = re.compile(r"^[A-ZА-ЯІЇЄҐ][a-zа-яіїєґ'\-]+$")
_UPPER_WORD_RE = re.compile(r"(?<![\w'])[A-ZА-ЯІЇЄҐ'’]{4,}(?![\w'])")

# Великі міста (обласні центри та міста-мільйонники/сотнетисячники, у т.ч. за кордоном) та країни:
# для них населення в шапці не вказують
_BIG_PLACES = {
    # Україна
    "київ", "харків", "одеса", "дніпро", "донецьк", "запоріжжя", "львів", "кривийріг", "миколаїв",
    "маріуполь", "луганськ", "вінниця", "херсон", "полтава", "чернігів", "черкаси", "хмельницький",
    "чернівці", "житомир", "суми", "рівне", "івано-франківськ", "франківськ", "кропивницький", "тернопіль",
    "луцьк", "ужгород", "білацерква", "кременчук", "краматорськ", "мелітополь", "кам'янське", "бровари",
    "павлоград", "нікополь", "слов'янськ", "бердянськ", "кам'янець-подільський", "сімферополь", "севастополь",
    "ірпінь", "дніпропетровськ",
    # рос. написання
    "киев", "харьков", "одесса", "днепр", "запорожье", "львов", "кривойрог", "николаев", "винница",
    "чернигов", "черкассы", "хмельницкий", "черновцы", "сумы", "ровно", "ивано-франковск", "тернополь",
    "луцк", "кременчуг", "полтава",
    # закордон
    "варшава", "краків", "вроцлав", "познань", "гданськ", "лодзь", "катовіце", "люблін", "щецин", "бидгощ",
    "прага", "берлін", "мюнхен", "гамбург", "відень", "лондон", "париж", "мадрид", "барселона", "рим",
    "мілан", "амстердам", "роттердам", "брюссель", "лісабон", "дублін", "таллінн", "рига", "вільнюс",
    "кишинів", "бухарест", "будапешт", "братислава", "стокгольм", "осло", "копенгаген", "гельсінкі",
    "нью-йорк", "чикаго", "торонто", "дубай", "стамбул", "анталія", "тбілісі", "батумі",
    # країни
    "україна", "польща", "німеччина", "іспанія", "італія", "франція", "чехія", "нідерланди", "англія",
    "великабританія", "сша", "канада", "португалія", "австрія", "ізраїль", "туреччина", "молдова",
}


# ------------------------------------------------------------------ parsing


@dataclass
class Segment:
    raw: str
    start: int
    end: int


@dataclass
class HeaderField:
    value: str  # очищене значення (без слів-замінників)
    start: int  # межі сирого фрагмента у вихідному тексті
    end: int


@dataclass
class HeaderParseResult:
    source: str
    segments_count: int = 0
    student_name: HeaderField | None = None
    business: HeaderField | None = None
    city: HeaderField | None = None
    package: HeaderField | None = None  # value — пакет+потік як у документі (канонічно)
    package_info: PackageInfo | None = None
    flow_number: HeaderField | None = None
    marker: HeaderField | None = None
    marker_type: WorkType | None = None
    dz_number: str | None = None
    placeholders: list[str] = field(default_factory=list)
    name_incomplete: bool = False
    has_population: bool = False
    underscore_mode: bool = False  # назва файлу через «_» (так зберігають браузери/месенджери)

    def value(self, attr: str) -> str:
        f = getattr(self, attr)
        return f.value if f else ""

    @property
    def is_club(self) -> bool:
        return bool(self.package_info and self.package_info.club)

    @property
    def city_parts(self) -> list[str]:
        if not self.city:
            return []
        parts = re.split(r"[,/()_;]|\s-\s|\s–\s", self.city.value)
        return [p for p in (_CITY_PREFIX_RE.sub("", p.strip()) for p in parts) if p]


def split_segments(text: str, allow_underscore: bool = False) -> list[Segment]:
    if allow_underscore and not SEPARATOR_RE.search(text) and "_" in text:
        return _split(text, _UNDERSCORE_RE)
    segments = _split(text, SEPARATOR_RE)
    if len(segments) < 4 and "-" in text:
        loose = _split(text, _LOOSE_SEPARATOR_RE)
        if len(loose) >= 4:
            return loose
    return segments


def _split(text: str, pattern: re.Pattern[str]) -> list[Segment]:
    segments: list[Segment] = []
    pos = 0
    for m in pattern.finditer(text):
        segments.append(_segment(text, pos, m.start()))
        pos = m.end()
    segments.append(_segment(text, pos, len(text)))
    return [s for s in segments if s.raw]


def _segment(text: str, start: int, end: int) -> Segment:
    raw = text[start:end]
    lead = len(raw) - len(raw.lstrip(" \t-–—"))
    trail = len(raw) - len(raw.rstrip(" \t-–—"))
    return Segment(raw=raw.strip(" \t-–—"), start=start + lead, end=end - trail)


def marker_type(segment_text: str) -> tuple[WorkType | None, str | None]:
    text = segment_text.strip()
    m = _DZ_MARKER_RE.match(text)
    if m:
        return WorkType.DZ, m.group("num")
    if _STRATEGIC_RE.search(text):
        return WorkType.STRATEGIC_QUESTION, None
    if _HYPOTHESIS_RE.search(text):
        return WorkType.HYPOTHESIS, None
    return None, None


def detect_work_types(text: str) -> set[WorkType]:
    """Усі типи робіт, згадані в рядку (для класифікації за назвою / першим рядком)."""
    found: set[WorkType] = set()
    if _DZ_ANYWHERE_RE.search(text):
        found.add(WorkType.DZ)
    if _STRATEGIC_RE.search(text):
        found.add(WorkType.STRATEGIC_QUESTION)
    elif _HYPOTHESIS_RE.search(text):
        found.add(WorkType.HYPOTHESIS)
    return found


def _strip_placeholders(text: str, words: tuple[str, ...] | None = None) -> str:
    for word, pattern in _PLACEHOLDER_PATTERNS.items():
        if words is None or word in words:
            text = pattern.sub(" ", text)
    return normalize(text).strip(" .,:;")


def _looks_like_name(text: str) -> bool:
    words = [w.strip(",.") for w in text.split()]
    return len(words) >= 2 and all(_NAME_WORD_RE.match(w) for w in words[:2])


def _is_template_literal(text: str) -> bool:
    return normalize(text).lower().strip(" .") in _TEMPLATE_LITERALS


def parse_header(
    text: str, allow_underscore: bool = False, bold_positions: set[int] | None = None
) -> HeaderParseResult:
    """bold_positions — позиції жирних символів першого рядка: допомагають відділити назву бізнесу
    (жирну) від міста, коли сегментів більше, ніж полів."""
    text = text or ""
    segments = split_segments(text, allow_underscore)
    result = HeaderParseResult(
        source=text,
        segments_count=len(segments),
        underscore_mode=allow_underscore and not SEPARATOR_RE.search(text) and "_" in text,
    )
    if not segments:
        return result

    # «ДЗ№7 Мельник Остап – …» — позначення на початку замість кінця
    lead = _DZ_MARKER_RE.match(segments[0].raw)
    if lead and lead.end() < len(segments[0].raw):
        seg0 = segments[0]
        result.marker = HeaderField(seg0.raw[: lead.end()].strip(), seg0.start, seg0.start + lead.end())
        result.marker_type = WorkType.DZ
        result.dz_number = lead.group("num")
        remainder = seg0.raw[lead.end() :]
        shift = len(remainder) - len(remainder.lstrip())
        segments[0] = Segment(remainder.strip(), seg0.start + lead.end() + shift, seg0.end)

    # «Іваненко Олена-дитячі клуби розвитку- м.Кривий Ріг – …»: ім'я і бізнес злиті дефісом без пробілів
    split = _split_name_hyphen(segments[0], bold_positions)
    if split:
        segments[0:1] = split
        result.segments_count = len(segments)

    for word, pattern in _PLACEHOLDER_PATTERNS.items():
        if any(pattern.search(s.raw) for s in segments[1:]):
            result.placeholders.append(word)

    # 1. Позначення типу роботи — останній сегмент, що його містить
    marker_idx: int | None = None
    for idx in range(len(segments) - 1, 0 if result.marker is None else len(segments) - 1, -1):
        wtype, num = marker_type(segments[idx].raw)
        if wtype:
            marker_idx = idx
            seg = segments[idx]
            result.marker = HeaderField(seg.raw, seg.start, seg.end)
            result.marker_type = wtype
            result.dz_number = num
            break

    content_end = marker_idx if marker_idx is not None else len(segments)
    used: set[int] = set() if marker_idx is None else {marker_idx}

    # 2. Пакет (разом із потоком)
    package_idx: int | None = None
    for idx in range(1, content_end):
        info = parse_package(segments[idx].raw)
        if info:
            seg = segments[idx]
            result.package = HeaderField(info.canonical, seg.start, seg.end)
            result.package_info = info
            if info.flow:
                result.flow_number = HeaderField(info.flow, seg.start, seg.end)
            package_idx = idx
            used.add(idx)
            break

    # 3. Потік окремим сегментом («… – ВІП – 67 – ДЗ №2»)
    if result.flow_number is None:
        for idx in range(len(segments) - 1, 0, -1):
            if idx not in used and _FLOW_RE.match(segments[idx].raw):
                seg = segments[idx]
                digits = re.search(r"\d+", seg.raw)
                result.flow_number = HeaderField(digits.group(0) if digits else seg.raw, seg.start, seg.end)
                used.add(idx)
                break

    # 4. Ім'я, бізнес, місто — по порядку до пакета
    limit = package_idx if package_idx is not None else content_end
    rest_idx = [i for i in range(limit) if i not in used]
    template_positions = [i for i in rest_idx if _is_template_literal(segments[i].raw)]
    if template_positions:
        _assign_positionally(result, segments, rest_idx, template_positions)
        return result

    rest: list[Segment | None] = [segments[i] for i in rest_idx]
    if rest and len(rest) < 3 and not _looks_like_name(rest[0].raw):
        rest = [None, *rest]  # ім'я відсутнє — зсуваємо
    _assign_main_fields(result, text, rest, bold_positions)
    return result


_NAME_HYPHEN_RE = re.compile(r"^([A-ZА-ЯІЇЄҐ][a-zа-яіїєґ'’ʼ]+\s+[A-ZА-ЯІЇЄҐ][a-zа-яіїєґ'’ʼ]+)-(?=\S)")


def _split_name_hyphen(seg: Segment, bold_positions: set[int] | None) -> list[Segment] | None:
    """«Прізвище Ім'я-бізнес»: дефіс одразу після імені — роздільник, якщо далі починається жирна назва
    бізнесу або слово з малої літери. «Олена Іванова-Петренко» (подвійне прізвище) не розриваємо."""
    m = _NAME_HYPHEN_RE.match(seg.raw)
    if not m:
        return None
    rest_start = m.end()
    following = seg.raw[rest_start]
    starts_bold = bool(bold_positions) and (seg.start + rest_start) in bold_positions
    if not (starts_bold or following.islower()):
        return None
    name = Segment(m.group(1), seg.start, seg.start + len(m.group(1)))
    rest = seg.raw[rest_start:].strip()
    return [name, Segment(rest, seg.end - len(rest), seg.end)] if rest else None


def _bold_share(seg: Segment, bold_positions: set[int]) -> float:
    letters = [i for i in range(seg.start, seg.end) if i < seg.end and not seg.raw[i - seg.start].isspace()]
    return sum(1 for i in letters if i in bold_positions) / len(letters) if letters else 0.0


def _assign_main_fields(
    result: HeaderParseResult, text: str, rest: list[Segment | None], bold_positions: set[int] | None
) -> None:
    if not rest:
        return
    name_seg = rest[0]
    if name_seg is not None:
        result.student_name = HeaderField(normalize(name_seg.raw), name_seg.start, name_seg.end)
        result.name_incomplete = len(name_seg.raw.split()) < 2
    if len(rest) < 2 or rest[1] is None:
        return
    if len(rest) <= 3:
        result.business = HeaderField(normalize(rest[1].raw), rest[1].start, rest[1].end)
        if len(rest) == 3 and rest[2] is not None:
            _set_city(result, rest[2])
        return

    # Сегментів більше, ніж полів: «Бізнес – частина бізнесу – Місто – Область».
    # Бізнес — жирні сегменти після імені; без даних про жирність — усе, крім останнього.
    middle = [s for s in rest[1:] if s is not None]
    split_at = len(middle) - 1
    if bold_positions:
        split_at = 1
        while split_at < len(middle) - 1 and _bold_share(middle[split_at], bold_positions) >= 0.5:
            split_at += 1
    # «Бізнес – Кременчуг – Полтава»: місто починається з першого сегмента, схожого на населений пункт
    place_at = next((i for i in range(1, len(middle)) if _looks_like_place(middle[i].raw)), None)
    if place_at is not None:
        # «Брусилів – Житомирська обл.»: перед областю/районом стоїть сам населений пункт
        if place_at > 1 and _REGION_RE.search(middle[place_at].raw) and not _looks_like_town(middle[place_at].raw):
            place_at -= 1
        split_at = min(split_at, place_at)
    business, city = middle[:split_at], middle[split_at:]
    result.business = HeaderField(
        normalize(text[business[0].start : business[-1].end]), business[0].start, business[-1].end
    )
    _set_city(result, Segment(text[city[0].start : city[-1].end], city[0].start, city[-1].end))


def _set_city(result: HeaderParseResult, seg: Segment) -> None:
    value = _strip_placeholders(seg.raw, ("місто",))
    result.city = HeaderField(normalize(value), seg.start, seg.end)
    result.has_population = bool(re.search(r"\d", seg.raw))


def _assign_positionally(
    result: HeaderParseResult, segments: list[Segment], positions: list[int], templates: list[int]
) -> None:
    """Студент залишив шаблонні слова («Місто», «Бізнес») — розбираємо строго за позицією."""
    for slot, idx in zip(("student_name", "business", "city"), positions):
        if idx in templates:
            continue
        seg = segments[idx]
        if slot == "city":
            _set_city(result, seg)
        else:
            setattr(result, slot, HeaderField(normalize(seg.raw), seg.start, seg.end))


def _place_key(text: str) -> str:
    return re.sub(r"[\s'’ʼ`]", "", normalize(text).casefold()).replace("ʼ", "")


_BIG_PLACE_KEYS = {_place_key(b) for b in _BIG_PLACES}
# «м. Стрий», «смт Брусилів», «Рівненська обл.», «Броварський район», «Березівка (7 000)»
_TOWN_RE = re.compile(r"^(?:м|г|с|смт)\s*\.|^(?:місто|город|смт|село)\s|\(\s*\d", re.IGNORECASE)
_REGION_RE = re.compile(r"\bобл\.|\bобл\b|област[ьі]\b|\bрайон\b|\bр-н\b", re.IGNORECASE)


def _looks_like_town(text: str) -> bool:
    text = normalize(text).strip(" ,.")
    core = re.sub(r"\(.*?\)", "", _CITY_PREFIX_RE.sub("", text)).strip(" ,.")
    return bool(_TOWN_RE.search(text)) or (bool(core) and _place_key(core) in _BIG_PLACE_KEYS)


def is_big_place(parts: list[str]) -> bool:
    keys = {_place_key(p) for p in parts} | {_place_key(w) for p in parts for w in p.split()}
    return any(k in _BIG_PLACE_KEYS for k in keys)


def _looks_like_place(text: str) -> bool:
    """Сегмент шапки — населений пункт/область: є «м.», «обл.», населення в дужках або це відоме місто цілком
    (слово «Київ» усередині назви бізнесу містом не вважається)."""
    return _looks_like_town(text) or bool(_REGION_RE.search(text))


# ----------------------------------------------------------------- checking


def check_header(
    parsed: HeaderParseResult,
    work_type: WorkType,
    area: Area,
    settings: Settings,
    issues: IssueCollector,
) -> None:
    where = "у назві файлу" if area == Area.FILE_NAME else "у першому рядку"
    if not parsed.source.strip():
        return

    if parsed.segments_count < 3:
        issues.add(
            "wrong_header_format",
            f"{where.capitalize()}: «{parsed.source.strip()[:120]}»",
            area,
        )
        return
    if parsed.underscore_mode:
        # «Прізвище_Ім'я_Бізнес_…» — поля не розділити однозначно; у відмовах на це не зважають
        return

    if parsed.student_name is None:
        issues.add("missing_student_name", f"Не знайдено {where}", area)
    elif parsed.name_incomplete:
        issues.add(
            "missing_student_name",
            f"{where.capitalize()} вказано лише «{parsed.student_name.value}», потрібно «Прізвище Ім'я»",
            area,
        )
    if parsed.business is None:
        issues.add("missing_business", f"Не знайдено {where}", area)
    if parsed.city is None or not parsed.city.value:
        issues.add("missing_city", f"Не знайдено {where}", area)
    if parsed.package is None:
        issues.add("missing_package", f"Не знайдено {where}", area)
    if settings.require_flow_number and parsed.package is not None and parsed.flow_number is None:
        issues.add("missing_flow_number", f"{where.capitalize()} пакет вказано без номера потоку", area)

    if parsed.marker is None:
        issues.add("missing_work_type_marker", f"Не знайдено {where}", area)
    elif work_type == WorkType.DZ and parsed.marker_type == WorkType.DZ and not parsed.dz_number:
        issues.add("missing_dz_number", f"{where.capitalize()} вказано «{parsed.marker.value}»", area)

    if parsed.placeholders:
        issues.add("forbidden_placeholder_word", f"Знайдено {where}: {quote_list(parsed.placeholders)}", area)

    if (
        work_type in (WorkType.HYPOTHESIS, WorkType.STRATEGIC_QUESTION)
        and parsed.city is not None
        and not parsed.has_population
        and not is_big_place(parsed.city_parts)
    ):
        issues.add("missing_population", f"{where.capitalize()}: «{parsed.city.value}»", area)

    if area == Area.STRUCTURE:
        _check_uppercase(parsed, issues)


def _check_uppercase(parsed: HeaderParseResult, issues: IssueCollector) -> None:
    skip = [(f.start, f.end) for f in (parsed.package, parsed.marker, parsed.flow_number) if f is not None]
    words = [
        m.group(0)
        for m in _UPPER_WORD_RE.finditer(parsed.source)
        if not any(s <= m.start() < e for s, e in skip)
    ]
    if words:
        issues.add("uppercase_in_header", f"Знайдено: {quote_list(words)}")


def check_header_consistency(
    from_file: HeaderParseResult, from_text: HeaderParseResult, issues: IssueCollector
) -> None:
    if from_file.segments_count < 3 or from_text.segments_count < 3 or from_file.underscore_mode:
        return
    mismatches: list[str] = []
    for attr, label in (("student_name", "прізвище та ім'я"), ("business", "бізнес")):
        a, b = from_file.value(attr), from_text.value(attr)
        ca, cb = _comparable(a), _comparable(b)
        # У назві файлу немає жирного шрифту, тож межа «бізнес / місто» може зсунутися на сегмент
        if a and b and not (ca.startswith(cb) or cb.startswith(ca)):
            mismatches.append(f"{label}: «{a}» / «{b}»")
    city_a, city_b = from_file.city_parts, from_text.city_parts
    words_a = {_comparable(w) for p in city_a for w in p.split()}
    words_b = {_comparable(w) for p in city_b for w in p.split()}
    if words_a and words_b and not words_a & words_b:
        mismatches.append(f"місто: «{city_a[0]}» / «{city_b[0]}»")
    pa, pb = from_file.package_info, from_text.package_info
    if pa and pb and (pa.canonical != pb.canonical or (pa.flow and pb.flow and pa.flow != pb.flow)):
        mismatches.append(f"пакет: «{pa.display}» / «{pb.display}»")
    if from_file.marker_type and from_text.marker_type and from_file.marker_type != from_text.marker_type:
        mismatches.append(f"тип роботи: «{from_file.marker.value}» / «{from_text.marker.value}»")
    if from_file.dz_number and from_text.dz_number and from_file.dz_number != from_text.dz_number:
        mismatches.append(f"номер ДЗ: «{from_file.dz_number}» / «{from_text.dz_number}»")
    if mismatches:
        issues.add("header_mismatch", "Назва файлу / перший рядок — " + "; ".join(mismatches))


def _comparable(value: str) -> str:
    return re.sub(r"[\W_]", "", normalize(value).casefold())
