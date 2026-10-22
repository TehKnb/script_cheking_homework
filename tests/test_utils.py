import pytest

from app.utils.numbers import find_badly_formatted_numbers, format_number, parse_number


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10.000 грн", 10000),
        ("1.200.000", 1200000),
        ("12,5", 12.5),
        ("3.5", 3.5),
        ("10 000", 10000),
        ("150 тис.", 150000),
        ("1,2 млн", 1200000),
        ("1.000,50", 1000.5),
    ],
)
def test_parse_number(text, expected):
    assert parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Прибуток 10000 $", ["10000"]),
        ("Прибуток 10 000 $", []),
        ("Прибуток 10,000 $", []),
        ("Прибуток 10.000 $ і 1.000 клієнтів", []),
        ("У 2024 році", []),
        ("Сезон 2024-2025", []),
        ("Бюджет 2024$", ["2024"]),
        ("ДЗ №1234", []),
        ("Телефон 0671234567", []),
        ("Дата 21.10.2026", []),
        ("Сайт https://example.com/12345", []),
        ("Всього 999 клієнтів", []),
    ],
)
def test_badly_formatted_numbers(text, bad):
    assert find_badly_formatted_numbers(text) == bad


def test_min_value_threshold():
    assert find_badly_formatted_numbers("1780 $ і 15000 $", min_value=10000) == ["15000"]


def test_format_number():
    assert format_number(1200000) == "1 200 000"
    assert format_number(500) == "500"
    assert format_number(12.5) == "12,5"


def test_spelling_ignores_course_jargon():
    from app.checkers.spelling import _is_jargon

    assert _is_jargon("таргетолога") and _is_jargon("рілз") and _is_jargon("лідів")
    assert not _is_jargon("обладнаня")
