"""Нормалізація тексту."""

from __future__ import annotations

import re
import unicodedata

_APOSTROPHES = "'’ʼ‘`´"
_SPACES_RE = re.compile(r"[ \t    ]+")

# Латинські літери, візуально схожі на кириличні (студенти часто пишуть «Точка A» латиницею)
_LATIN_TO_CYRILLIC = str.maketrans(
    {
        "A": "А", "a": "а", "B": "В", "C": "С", "c": "с", "E": "Е", "e": "е",
        "H": "Н", "I": "І", "i": "і", "K": "К", "M": "М", "O": "О", "o": "о",
        "P": "Р", "p": "р", "T": "Т", "X": "Х", "x": "х", "y": "у",
    }
)

URL_RE = re.compile(
    r"(?:https?://|ftp://|www\.)[^\s<>«»\"]+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|ua|ru|io|me|info|biz|link|ly|gl|app|site|online|shop|store)"
    r"(?:/[^\s<>«»\"]*)?",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """NFC, уніфіковані апострофи та пробіли, без крайових пробілів."""
    text = unicodedata.normalize("NFC", text or "")
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    return _SPACES_RE.sub(" ", text).strip()


def fold_lookalikes(text: str) -> str:
    """Замінює латинські літери-двійники кириличними (для пошуку заголовків)."""
    return text.translate(_LATIN_TO_CYRILLIC)


# Символи розмітки Telegram (Markdown: _ * ` [ ], HTML: < > &) у тексті зауважень ламають повідомлення n8n
# («Can't find end of the entity»). Замінюємо їх схожими на вигляд символами, які розміткою не є.
_TELEGRAM_SAFE = str.maketrans({
    "_": "＿", "*": "∗", "`": "'", "[": "［", "]": "］", "<": "＜", ">": "＞", "&": "＆",
})


def telegram_safe(text: str) -> str:
    return text.translate(_TELEGRAM_SAFE)


def shorten(text: str, limit: int = 60) -> str:
    text = normalize(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def quote_list(items: list[str], limit: int = 5) -> str:
    """«a», «b», «c» (+ще N)."""
    unique: list[str] = []
    for item in items:
        if item not in unique:
            unique.append(item)
    shown = ", ".join(f"«{shorten(i, 40)}»" for i in unique[:limit])
    rest = len(unique) - limit
    return shown + (f" (і ще {rest})" if rest > 0 else "")
