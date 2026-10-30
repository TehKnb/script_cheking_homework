import pytest

from app.checkers.classification import classify_work_type
from app.checkers.header import detect_work_types, check_header, parse_header, parse_package
from app.issues import IssueCollector
from app.models import Area, WorkType
from tests.conftest import make_settings


def _codes(text: str, work_type=WorkType.DZ, area=Area.STRUCTURE, **settings_overrides) -> set[str]:
    issues = IssueCollector()
    check_header(parse_header(text), work_type, area, make_settings(**settings_overrides), issues)
    return {i.code for i in issues.issues}


def test_parse_full_dz_header():
    parsed = parse_header("Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №3")
    assert parsed.student_name.value == "Іваненко Іван"
    assert parsed.business.value == "Кав'ярня"
    assert parsed.city.value == "Київ"
    assert parsed.package.value == "ВІП"
    assert parsed.flow_number.value == "67"
    assert parsed.marker_type == WorkType.DZ
    assert parsed.dz_number == "3"
    assert _codes(parsed.source) == set()


@pytest.mark.parametrize(
    ("text", "canonical", "flow", "club"),
    [
        ("ВІП67", "ВІП", "67", False),
        ("ВІП 67+", "ВІП+", "67", True),
        ("ВІП+ 66", "ВІП+", "66", True),
        ("VIP67", "ВІП", "67", False),
        ("Стандарт67", "Стандарт", "67", False),
        ("Річна63", "Річна", "63", True),
        ("РІЧНА63", "Річна", "63", True),
        ("Річний 65+", "Річна", "65", True),
        ("Річна ВІП 58", "ВІП+", "58", True),
        ("КМ+ 49", "КМ+", "49", True),
        ("пакет ВІП", "ВІП", None, False),
    ],
)
def test_parse_package_variants(text, canonical, flow, club):
    info = parse_package(text)
    assert (info.canonical, info.flow, info.club) == (canonical, flow, club)


def test_flow_number_as_separate_segment():
    parsed = parse_header("Петренко Олена – Салон краси – Львів – Стандарт – 12 – ДЗ №1")
    assert parsed.flow_number.value == "12"
    assert parsed.package.value == "Стандарт"


def test_hyphen_separators_and_typographic_apostrophe():
    parsed = parse_header("Іваненко Іван - Кав’ярня - Київ - Річна63 - Гіпотеза")
    assert parsed.business.value == "Кав'ярня"
    assert parsed.package.value == "Річна"
    assert parsed.marker_type == WorkType.HYPOTHESIS


def test_hyphens_without_spaces():
    parsed = parse_header("Коваль Ірина-Штори-карнизи-Черкаси-Річна63-Гіпотеза")
    assert parsed.student_name.value == "Коваль Ірина"
    assert parsed.business.value == "Штори-карнизи"
    assert parsed.city.value == "Черкаси"


def test_city_prefix_and_population_are_allowed():
    parsed = parse_header("Бондар Марія – Юридичні послуги – м. Березівка (7 000), Одеська обл. – Річна63 – Гіпотеза")
    assert parsed.has_population
    assert _codes(parsed.source, WorkType.HYPOTHESIS) == set()


def test_population_required_for_small_town_hypothesis():
    text = "Ткачук Андрій – Буріння – м. Борислав, Львівська обл. – ВІП65 – Гіпотеза"
    assert "missing_population" in _codes(text, WorkType.HYPOTHESIS)
    assert "missing_population" not in _codes(text.replace("Гіпотеза", "ДЗ №2"), WorkType.DZ)
    assert "missing_population" not in _codes(
        "Іваненко Іван – Кафе – Харків/Іспанія – Річна30 – Гіпотеза", WorkType.HYPOTHESIS
    )


def test_placeholder_words_are_reported_but_values_extracted():
    parsed = parse_header("Іваненко Іван – Кав'ярня – Київ – пакет ВІП – 5 потік – ДЗ №2")
    assert parsed.package.value == "ВІП"
    assert parsed.flow_number.value == "5"
    assert set(parsed.placeholders) == {"пакет", "потік"}
    assert "forbidden_placeholder_word" in _codes(parsed.source)


def test_uppercase_words():
    codes = _codes("Шевчук Олена – ЮРИДИЧНИЙ СУПРОВІД – Вишково (5 000) – ВІП67 – ДЗ №1")
    assert "uppercase_in_header" in codes
    assert "uppercase_in_header" not in _codes("Гончар Юлія – Подарунки – Хмельницький – РІЧНА63 – ДЗ №1")


def test_business_with_extra_segments_uses_bold():
    text = "Сидоренко Максим - Студия покраски - Кременчуг - Полтава – ВІП67 – ДЗ№2"
    bold = set(range(text.index("Студия"), text.index("Студия") + len("Студия покраски")))
    parsed = parse_header(text, bold_positions=bold)
    assert parsed.business.value == "Студия покраски"
    assert parsed.city.value == "Кременчуг - Полтава"


@pytest.mark.parametrize(
    ("text", "business", "city"),
    [
        # назва файлу — даних про жирність немає
        (
            "Сидоренко Максим - Студия порошковой покраски и ремонта дисков - Кременчуг - Полтава – ВІП – ДЗ№2",
            "Студия порошковой покраски и ремонта дисков",
            "Кременчуг - Полтава",
        ),
        ("Іваненко Іван – Ремонт – авто – м. Стрий – Львівська обл. – ВІП67 – ДЗ №2", "Ремонт – авто", "м. Стрий – Львівська обл"),
        ("Іваненко Іван – Кав'ярня – Брусилів – Житомирська обл. – ВІП67 – ДЗ №2", "Кав'ярня", "Брусилів – Житомирська обл"),
        # невідоме місто без «м.»/«обл.» — як і раніше, місто — останній сегмент
        ("Іваненко Іван – Ремонт – авто – Брусилів – ВІП67 – ДЗ №2", "Ремонт – авто", "Брусилів"),
        # «Київ» усередині назви бізнесу містом не є
        ("Іваненко Іван – Кава Київ – доставка – Львів – ВІП67 – ДЗ №2", "Кава Київ – доставка", "Львів"),
    ],
)
def test_city_with_region_without_bold(text, business, city):
    parsed = parse_header(text)
    assert parsed.business.value == business
    assert parsed.city.value == city


def test_marker_at_the_beginning():
    parsed = parse_header("ДЗ№7 Мельник Остап - дитячі іграшки - Звягель - Стандарт62")
    assert parsed.marker_type == WorkType.DZ and parsed.dz_number == "7"
    assert parsed.student_name.value == "Мельник Остап"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Кав'ярня – Київ – ВІП67 – ДЗ №3", {"missing_student_name"}),
        ("Іваненко Іван – Кав'ярня – ВІП67 – ДЗ №3", {"missing_city"}),
        ("Іваненко Іван – Кав'ярня – Київ – ДЗ №3", {"missing_package"}),
        ("Іваненко Іван – Кав'ярня – Київ – Річна – ДЗ №3", {"missing_flow_number"}),
        ("Іваненко Іван – Кав'ярня – Київ – Річна ВІП – ДЗ №3", {"missing_flow_number"}),
        ("Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ", {"missing_dz_number"}),
        ("Іваненко Іван – Кав'ярня – Місто – ВІП67 – ДЗ №3", {"missing_city", "forbidden_placeholder_word"}),
        ("ДЗ 2", {"wrong_header_format"}),
    ],
)
def test_missing_fields(text, expected):
    assert _codes(text) == expected


def test_flow_number_can_be_optional():
    assert "missing_flow_number" not in _codes("Іваненко Іван – Кафе – Київ – Річна – ДЗ №3", require_flow_number=False)


@pytest.mark.parametrize("package", ["Економ", "Стандарт", "ВІП", "ВІП+", "КМ+", "ПРОРИВ", "Прорив 12", "Платинум КМ"])
def test_flow_number_required_only_for_annual(package):
    assert _codes(f"Іваненко Іван – Кафе – Київ – {package} – ДЗ №3") == set()


@pytest.mark.parametrize(
    ("package", "canonical", "club"),
    [("Економ67", "Економ", False), ("ПРОРИВ", "ПРОРИВ", False), ("Платинум КМ 49", "Платинум КМ", True)],
)
def test_new_packages(package, canonical, club):
    parsed = parse_header(f"Іваненко Іван – Кафе – Київ – {package} – Гіпотеза")
    assert parsed.package_info.canonical == canonical and parsed.is_club == club


def test_work_type_marker_optional_for_hypothesis_and_question():
    for work_type in (WorkType.HYPOTHESIS, WorkType.STRATEGIC_QUESTION):
        issues = IssueCollector()
        check_header(parse_header("Іваненко Іван – Кафе – Київ – ВІП67"), work_type, Area.STRUCTURE, make_settings(), issues)
        assert issues.issues == []
    issues = IssueCollector()
    check_header(parse_header("Іваненко Іван – Кафе – Київ – ВІП67"), WorkType.DZ, Area.STRUCTURE, make_settings(), issues)
    assert [i.code for i in issues.issues] == ["missing_work_type_marker"]


@pytest.mark.parametrize("dash", ["-", "‐", "−"])
def test_package_and_dz_in_one_segment(dash):
    text = f"Іваненко Іван – Вирощування квітів – м.Радивилів, Рівненська область {dash} ВІП67 ДЗ №6"
    parsed = parse_header(text)
    assert parsed.package_info.display == "ВІП67"
    assert parsed.city.value == "м.Радивилів, Рівненська область"
    assert parsed.marker_type == WorkType.DZ and parsed.dz_number == "6"
    assert _codes(text) == set()


@pytest.mark.parametrize("marker", ["Д.З№6", "Д,З №6", "Д. З. №6", "Д/З 6"])
def test_dz_marker_with_dot_or_comma(marker):
    parsed = parse_header(f"Іваненко Іван – стоматологія-Калуш- ВІП67+ {marker}")
    assert parsed.package_info.display == "ВІП+67" and parsed.city.value == "Калуш"
    assert parsed.marker_type == WorkType.DZ and parsed.dz_number == "6"


def test_package_glued_to_city_by_hyphen():
    parsed = parse_header("Іваненко Іван- Товари для дому – Чернівці-ВІП-67 - ДЗ №6")
    assert parsed.city.value == "Чернівці" and parsed.package_info.display == "ВІП67"
    assert parsed.dz_number == "6"


def test_dz_marker_in_file_name_only():
    assert detect_work_types("Д,З №6") == {WorkType.DZ}


def test_underscore_file_name_with_dash_is_lenient():
    issues = IssueCollector()
    parsed = parse_header("Іваненко_Іван_—_вирощування_квітів_м_Радивилів,", allow_underscore=True)
    check_header(parsed, WorkType.DZ, Area.FILE_NAME, make_settings(), issues)
    assert parsed.underscore_mode and issues.issues == []


def test_underscore_file_names_are_lenient():
    issues = IssueCollector()
    parsed = parse_header("Кравець_Іван_Автосервіс_Харків_ВІП67_Гіпотеза", allow_underscore=True)
    check_header(parsed, WorkType.HYPOTHESIS, Area.FILE_NAME, make_settings(), issues)
    assert issues.issues == []


@pytest.mark.parametrize(
    ("file_name", "first_line", "body", "expected"),
    [
        ("Іваненко – ДЗ №1", "Іваненко Іван – Кафе – Київ – ВІП67 – ДЗ №1", "", WorkType.DZ),
        ("робота", "Іваненко Іван – Кафе – Київ – ВІП67 – Гіпотеза", "", WorkType.HYPOTHESIS),
        ("Іваненко Іван – Кафе – Київ – ВІП67 – Стратегічне питання", "", "", WorkType.STRATEGIC_QUESTION),
        ("Лисенко Василь – Будівництво – Володимир – ВІП67 – Важливе питання", "", "", WorkType.STRATEGIC_QUESTION),
        ("Іваненко – ДЗ №1", "Іваненко Іван – Кафе – Київ – ВІП67 – Гіпотеза", "", WorkType.UNKNOWN),
        ("робота", "Іваненко Іван – Кафе – Київ – ВІП67", "Ціна питання: 12 000$", WorkType.HYPOTHESIS),
        ("робота", "Іваненко Іван – Кафе – Київ – ВІП67", "Моє питання стратегічне, оскільки", WorkType.STRATEGIC_QUESTION),
        ("робота", "Просто текст", "", WorkType.UNKNOWN),
    ],
)
def test_classification(file_name, first_line, body, expected):
    assert classify_work_type(parse_header(file_name), parse_header(first_line), body) == expected


@pytest.mark.parametrize(
    ("text", "name", "business", "city"),
    [
        # дефіс без пробілів між ім'ям і бізнесом
        ("Бойко Тетяна-дитячі клуби розвитку- м.КривийРіг - Стандарт 67- ДЗ№2", "Бойко Тетяна", "дитячі клуби розвитку", "м.КривийРіг"),
        # подвійне прізвище не розриваємо
        ("Олена Іванова-Петренко – Салон – Львів – ВІП67 – ДЗ №1", "Олена Іванова-Петренко", "Салон", "Львів"),
    ],
)
def test_name_joined_to_business_by_hyphen(text, name, business, city):
    parsed = parse_header(text)
    assert (parsed.student_name.value, parsed.business.value, parsed.city.value) == (name, business, city)


def test_name_hyphen_split_by_bold_business():
    text = "Бойко Тетяна-Дитячі клуби – Київ – ВІП67 – ДЗ №2"
    start = text.index("Дитячі")
    parsed = parse_header(text, bold_positions=set(range(start, start + len("Дитячі клуби"))))
    assert parsed.student_name.value == "Бойко Тетяна" and parsed.business.value == "Дитячі клуби"
