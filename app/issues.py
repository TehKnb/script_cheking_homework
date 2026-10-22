"""Каталог кодів помилок і збирач issues.

Кожен код має область за замовчуванням, рівень серйозності та базовий опис українською
(формулювання максимально близькі до текстів відмов, які пишуть співробітники).
Серйозність `MANUAL` означає, що роботу треба віддати на ручну перевірку
(автомат не може надійно оцінити файл), `REVISION` — повернути студенту.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models import Area, Issue


class Severity(str, Enum):
    REVISION = "revision"
    MANUAL = "manual"


@dataclass(frozen=True)
class IssueSpec:
    area: Area
    severity: Severity
    description: str


ISSUE_CATALOG: dict[str, IssueSpec] = {
    # --- Файл ---
    "unreadable_file": IssueSpec(
        Area.FILE_FORMAT, Severity.MANUAL, "Файл пошкоджений або не може бути прочитаний"
    ),
    "processing_error": IssueSpec(
        Area.FILE_FORMAT, Severity.MANUAL, "Під час автоматичної перевірки сталася внутрішня помилка"
    ),
    "conversion_unavailable": IssueSpec(
        Area.FILE_FORMAT,
        Severity.MANUAL,
        "Не вдалося сконвертувати .doc у .docx: LibreOffice недоступний на сервері перевірки",
    ),
    "unsupported_file_format": IssueSpec(
        Area.FILE_FORMAT,
        Severity.REVISION,
        "Роботу потрібно виконувати в Microsoft Word і надсилати документом у форматі .doc або .docx "
        "(формати .pdf, .pages, .rtf, .wps, зображення та посилання на онлайн-редактори не приймаються)",
    ),
    "wrong_file_format": IssueSpec(
        Area.FILE_FORMAT,
        Severity.REVISION,
        "Базовий формат роботи — .doc. Формат .docx допускається лише для робіт з воронкою, "
        "а у документі воронку не знайдено",
    ),
    "landscape_orientation": IssueSpec(
        Area.FORMATTING,
        Severity.REVISION,
        "Робота виконана (повністю або частково) в альбомній орієнтації. "
        "ДЗ, Гіпотези та Стратегічні питання виконуються лише в книжковій орієнтації",
    ),
    # --- Класифікація ---
    "unclear_work_type": IssueSpec(
        Area.CLASSIFICATION,
        Severity.MANUAL,
        "Не вдалося визначити тип роботи (ДЗ, Гіпотеза чи Стратегічне питання) за назвою файлу, першим рядком і змістом",
    ),
    "work_type_mismatch": IssueSpec(
        Area.CLASSIFICATION,
        Severity.REVISION,
        "Тип роботи в назві не відповідає змісту. Оформіть роботу або як Гіпотезу "
        "(точка А, планована т.Б, ціна питання), або як Стратегічне питання",
    ),
    "short_content": IssueSpec(
        Area.STRUCTURE,
        Severity.MANUAL,
        "Робота має дуже мале наповнення — потрібно уточнити у куратора, чи приймати її",
    ),
    # --- Назва файлу / перший рядок ---
    "empty_document": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Документ не містить тексту"),
    "wrong_header_format": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Назва документа і перший рядок мають бути заповнені за форматом: "
        "Прізвище Ім'я – Бізнес – Місто – Пакет – ДЗ №N / Гіпотеза / Стратегічне питання",
    ),
    "missing_student_name": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Відсутні прізвище та ім'я студента"),
    "missing_business": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Відсутня назва бізнесу"),
    "missing_city": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Відсутнє місто"),
    "missing_population": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Для Гіпотез і Стратегічних питань у першому рядку після міста потрібно вказати область "
        "та кількість населення, напр.: «м. Березівка (7 000), Одеська обл.»",
    ),
    "missing_package": IssueSpec(
        Area.STRUCTURE, Severity.REVISION, "Відсутній або не розпізнаний пакет (Стандарт, ВІП, ВІП+, Річна)"
    ),
    "missing_flow_number": IssueSpec(
        Area.STRUCTURE, Severity.REVISION, "Відсутній номер потоку — його пишуть разом із пакетом, напр. «ВІП67»"
    ),
    "missing_dz_number": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Відсутній номер ДЗ (формат: «ДЗ №N»)"),
    "missing_work_type_marker": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Відсутнє позначення типу роботи в кінці («ДЗ №N», «Гіпотеза» або «Стратегічне питання»)",
    ),
    "forbidden_placeholder_word": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Використано зайві слова-замінники («пакет», «місто», «потік»); потрібно вказувати лише значення",
    ),
    "uppercase_in_header": IssueSpec(
        Area.FORMATTING, Severity.REVISION, "У першому рядку слова не потрібно писати великими літерами"
    ),
    "header_mismatch": IssueSpec(
        Area.FILE_NAME, Severity.REVISION, "Назва документа повинна бути такою ж, як перший рядок у роботі"
    ),
    # --- Оформлення ---
    "wrong_font": IssueSpec(Area.FORMATTING, Severity.REVISION, "Текст повинен бути шрифту Times New Roman"),
    "wrong_first_line_size": IssueSpec(Area.FORMATTING, Severity.REVISION, "Перший рядок має бути 14 розміру"),
    "wrong_body_text_size": IssueSpec(
        Area.FORMATTING, Severity.REVISION, "Текст повинен бути 12 розміру (крім першого рядка — він 14 розміру)"
    ),
    "wrong_bold_formatting": IssueSpec(
        Area.FORMATTING,
        Severity.REVISION,
        "У першому рядку жирним потрібно виділяти лише назву бізнесу та пакет з номером потоку",
    ),
    "wrong_package_color": IssueSpec(
        Area.FORMATTING,
        Severity.REVISION,
        "У першому рядку пакет потрібно виділяти кольором (Стандарт — синій, ВІП/ВІП+ — червоний, Річна — чорний)",
    ),
    "wrong_number_format": IssueSpec(
        Area.FORMATTING,
        Severity.REVISION,
        "Тисячні розряди треба розділяти пробілом або комою (1 000, 10 000, 25 000)",
    ),
    "wrong_currency": IssueSpec(
        Area.CALCULATION, Severity.REVISION, "Усі фінансові показники та розрахунки мають бути в доларах ($)"
    ),
    "missing_currency": IssueSpec(
        Area.CALCULATION, Severity.REVISION, "У числових значеннях показників обов'язково має бути вказана валюта ($)"
    ),
    "missing_space_after_punctuation": IssueSpec(
        Area.FORMATTING,
        Severity.REVISION,
        "Часто відсутній пробіл після розділових знаків (ком, крапок, двокрапок тощо), що ускладнює читання",
    ),
    "broken_characters": IssueSpec(
        Area.FORMATTING, Severity.REVISION, "У тексті присутні символи, які відображаються некоректно"
    ),
    "too_many_spelling_errors": IssueSpec(
        Area.FORMATTING, Severity.REVISION, "В тексті наявна значна кількість орфографічних помилок"
    ),
    # --- Гіпотеза / Стратегічне питання / ДЗ ---
    "missing_point_a": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Відсутня точка А"),
    "missing_point_b": IssueSpec(
        Area.STRUCTURE, Severity.REVISION, "Відсутня планована точка Б («Планована т.Б – …$»)"
    ),
    "missing_point_b_club": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Для річного доступу потрібно вказати точку Б після базової програми та точку Б (клуб)",
    ),
    "missing_growth_reason": IssueSpec(
        Area.STRUCTURE, Severity.REVISION, "Відсутній опис, за рахунок чого відбулось зростання/падіння"
    ),
    "missing_question_value": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Відсутня ціна питання"),
    "missing_hypothesis_description": IssueSpec(
        Area.STRUCTURE, Severity.REVISION, "Відсутній опис гіпотези («Моя гіпотеза полягає …»)"
    ),
    "missing_author_questions": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Відсутні питання автора"),
    "wrong_calculation": IssueSpec(
        Area.CALCULATION,
        Severity.REVISION,
        "Помилка в розрахунках: результат не відповідає виразу. Перевірте обчислення",
    ),
    "point_a_parts_mismatch": IssueSpec(
        Area.CALCULATION,
        Severity.REVISION,
        "Сума частин точки А (А1 + А2 + …) не дорівнює самій точці А (А = Кл × $)",
    ),
    "wrong_question_value_calculation": IssueSpec(
        Area.CALCULATION,
        Severity.REVISION,
        "Невірно прорахована ціна питання (формула: (Запланована точка Б − поточна точка) × 12)",
    ),
    "missing_strategic_reason": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Не розписано, чому питання стратегічне («Моє питання стратегічне, оскільки …», «Суть питання»)",
    ),
    "missing_solution_options": IssueSpec(
        Area.STRUCTURE, Severity.REVISION, "Не запропоновано варіанти вирішення питання"
    ),
    "missing_funnel": IssueSpec(Area.FUNNEL, Severity.REVISION, "У роботі відсутня воронка продажів"),
    "missing_landing_link": IssueSpec(
        Area.STRUCTURE, Severity.REVISION, "У ДЗ№7 потрібно надати посилання на опублікований лендинг"
    ),
    "missing_material_link": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Матеріали (креативи, файли на Google Диску) потрібно додати в саму роботу — посиланням з доступом "
        "для всіх, хто має посилання, або зображенням, а не надсилати окремо",
    ),
    # --- Посилання ---
    "link_not_clickable": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Посилання на інтернет-ресурс (що починається з http) має бути клікабельним гіперпосиланням",
    ),
    "link_requires_access": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Посилання у роботі не працює — воно потребує доступу. Відкрийте доступ для всіх, хто має посилання",
    ),
    "link_not_working": IssueSpec(Area.STRUCTURE, Severity.REVISION, "Посилання у роботі не відкривається"),
    "landing_editor_link": IssueSpec(
        Area.STRUCTURE,
        Severity.REVISION,
        "Надано посилання на редактор лендингу, а не на сам опублікований лендинг",
    ),
    # --- Воронка та візуальні матеріали ---
    "funnel_not_visual": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Воронка оформлена текстом або таблицею. Її потрібно оформити у Word пірамідою SmartArt (або зображенням)",
    ),
    "missing_funnel_conversion": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "У воронці не прорахована конверсія між кроками (має бути ліворуч між блоками, у %)",
    ),
    "wrong_funnel_conversion": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Невірно розрахована конверсія воронки (формула: наступний крок / попередній крок × 100%)",
    ),
    "wrong_conversion_position": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Конверсія у воронці повинна знаходитись ліворуч між кроками, а не в піраміді чи праворуч",
    ),
    "funnel_conversion_without_values": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "У центрі воронки мають бути цифри, з яких порахована конверсія. Додайте значення або приберіть конверсію",
    ),
    "missing_funnel_stage_label": IssueSpec(
        Area.FUNNEL, Severity.REVISION, "У воронці відсутній опис кроків (має бути праворуч від воронки)"
    ),
    "funnel_labels_inside": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Опис кроків воронки має бути праворуч від самих кроків, а не в центрі воронки",
    ),
    "funnel_fields_overlap": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Текстові поля з описами чи конверсією не повинні налазити на воронку і закривати її блоки",
    ),
    "funnel_labels_misaligned": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Описи кроків мають стояти праворуч рівно навпроти своїх блоків і на однаковій відстані від воронки",
    ),
    "funnel_label_count_mismatch": IssueSpec(
        Area.FUNNEL, Severity.REVISION, "У воронці описів кроків більше, ніж самих блоків воронки"
    ),
    "empty_funnel_steps": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "У воронці є пусті кроки. Їх потрібно заповнити (і прорахувати конверсію) або прибрати",
    ),
    "wrong_funnel_order": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Кроки воронки переплутані місцями: зверху має бути перший (найбільший) етап, внизу — останній",
    ),
    "small_text": IssueSpec(
        Area.FUNNEL,
        Severity.REVISION,
        "Воронки, оргструктури, таблиці чи скріншоти погано читаються через дрібний текст. "
        "Потрібно збільшити розмір шрифту або перебудувати структуру",
    ),
    "funnel_unreadable": IssueSpec(
        Area.FUNNEL, Severity.MANUAL, "Не вдалося автоматично розпізнати воронку — її потрібно перевірити вручну"
    ),
    "funnel_check_unavailable": IssueSpec(
        Area.FUNNEL,
        Severity.MANUAL,
        "Документ містить зображення, але OCR недоступний на сервері — воронку потрібно перевірити вручну",
    ),
}


class IssueCollector:
    """Накопичує issues без дублікатів і знає їхню серйозність."""

    def __init__(self) -> None:
        self._issues: list[Issue] = []
        self._severities: list[Severity] = []
        self._seen: set[tuple[str, str, str]] = set()

    def add(self, code: str, detail: str | None = None, area: Area | None = None) -> None:
        spec = ISSUE_CATALOG[code]
        description = spec.description if not detail else f"{spec.description}. {detail}"
        resolved_area = area or spec.area
        key = (code, resolved_area.value, description)
        if key in self._seen:
            return
        self._seen.add(key)
        self._issues.append(Issue(code=code, area=resolved_area, description=description))
        self._severities.append(spec.severity)

    def has(self, code: str) -> bool:
        return any(i.code == code for i in self._issues)

    @property
    def issues(self) -> list[Issue]:
        return list(self._issues)

    @property
    def requires_manual_review(self) -> bool:
        return Severity.MANUAL in self._severities

    def __len__(self) -> int:
        return len(self._issues)
