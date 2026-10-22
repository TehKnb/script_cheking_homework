"""Пошук і розбір чисел у тексті українських навчальних робіт.

Правило роздільників: крапка, після якої рівно три цифри, — роздільник розрядів (10.000 = 10000);
кома або крапка з іншою кількістю цифр — десятковий роздільник (12,5 / 12.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER_RE = re.compile(
    r"(?<![\d.,])"
    r"(?P<int>\d{1,3}(?:[.,   ]\d{3})+(?!\d)|\d+)"
    r"(?:(?P<dec_sep>[.,])(?P<dec>\d+))?"
    r"(?:\s?(?P<mult>тис\.?|тыс\.?|млн\.?|млрд\.?|k|к|m)(?![^\W\d_]))?",
    re.IGNORECASE,
)

_MULTIPLIERS = {"тис": 1e3, "тыс": 1e3, "k": 1e3, "к": 1e3, "млн": 1e6, "m": 1e6, "млрд": 1e9}



@dataclass(frozen=True)
class NumberMatch:
    raw: str
    int_raw: str
    value: float
    start: int
    end: int
    has_multiplier: bool


def _int_value(int_raw: str) -> int:
    return int("".join(ch for ch in int_raw if ch.isdigit()))


def find_numbers(text: str) -> list[NumberMatch]:
    result: list[NumberMatch] = []
    for m in NUMBER_RE.finditer(text):
        int_raw = m.group("int")
        value = float(_int_value(int_raw))
        dec = m.group("dec")
        if dec:
            # «1.000» уже розібрано як ціле; «.5»/«,50» — десяткова частина
            value += float(f"0.{dec}")
        mult = m.group("mult")
        if mult:
            value *= _MULTIPLIERS[mult.lower().rstrip(".")]
        result.append(
            NumberMatch(
                raw=m.group(0).strip(),
                int_raw=int_raw,
                value=value,
                start=m.start(),
                end=m.end(),
                has_multiplier=bool(mult),
            )
        )
    return result


def parse_number(text: str) -> float | None:
    """Перше число в рядку або None."""
    numbers = find_numbers(text)
    return numbers[0].value if numbers else None


def find_badly_formatted_numbers(text: str, min_value: int = 1000) -> list[str]:
    """Числа ≥ 1000, записані без роздільника розрядів («10000» замість «10 000»).

    Роздільником вважаються пробіл, кома або крапка (1 000 / 1,000 / 1.000).
    Не вважаються помилкою: роки (1950–2035, якщо після них не стоїть валюта чи %), номери («№1234»),
    числа з провідним нулем (телефони), довгі ідентифікатори (≥ 9 цифр), числа всередині URL.
    """
    masked = _mask_urls(text)
    bad: list[str] = []
    for m in NUMBER_RE.finditer(masked):
        int_raw = m.group("int")
        if not int_raw.isdigit() or len(int_raw) < 4 or int(int_raw) < min_value:
            continue  # уже з роздільником або замале
        if int_raw.startswith("0") or len(int_raw) >= 9:
            continue
        before = masked[max(0, m.start() - 2) : m.start()]
        if "№" in before or "#" in before or before.endswith(("+", "-", "/")):
            continue
        after = masked[m.end("int") :]
        if after[:1] in ("-", "/", ":"):
            continue  # діапазони років, дати, артикули
        if 1950 <= int(int_raw) <= 2035 and not _MONEY_AFTER_RE.match(after):
            continue
        bad.append(int_raw)
    return bad


_MONEY_AFTER_RE = re.compile(r"^\s?(?:\$|%|грн|₴|usd|uah|€|дол)", re.IGNORECASE)


def _mask_urls(text: str) -> str:
    from app.utils.text import URL_RE

    return URL_RE.sub(lambda m: " " * len(m.group(0)), text)


def format_number(value: float) -> str:
    """10000 -> «10 000», 12.5 -> «12,5» (у прийнятому в роботах форматі)."""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")
