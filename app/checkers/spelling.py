"""Орфографія через hunspell (словники uk_UA, ru_RU, en_US — роботи бувають і російською).

Перевіряються лише слова з малої літери: імена, назви брендів і міст з великої літери
дали б багато хибних спрацювань. Помилкою вважається висока частка слів, яких немає в жодному словнику.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

from app.config import Settings
from app.issues import IssueCollector
from app.services.document_loader import ParsedDocument
from app.utils.text import URL_RE, normalize, quote_list

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")
_MIN_MISSPELLED = 8
# Професійний сленг курсу та маркетингу, якого немає в словниках (основи слів)
_JARGON_STEMS = (
    "таргет", "рілз", "рілс", "сторіс", "сторис", "крео", "креатив", "лід", "лидоген", "ворд", "рекрут",
    "хайлайт", "автопостин", "директ", "інстаграм", "інста", "фейсбук", "тікток", "ютуб", "телеграм", "вайбер",
    "лендінг", "лендинг", "оффер", "офер", "трипваєр", "трипвайер", "допродаж", "апсейл", "кросейл", "ретаргет",
    "смм", "контент", "чек-лист", "чеклист", "дашборд", "фідбек", "кейс", "воркшоп", "промо", "охват", "клік",
    "конверс", "менеджер", "бренд", "блогер", "інфлюенс", "скрипт", "сегмент", "мессенджер", "месенджер",
    "онлайн", "офлайн", "оффлайн", "шоурум", "маркетплейс", "дропшип", "фриланс", "дизайн", "аутсорс",
)


def _is_jargon(word: str) -> bool:
    lowered = word.lower()
    return lowered.startswith(_JARGON_STEMS)


def find_misspelled(words: list[str], settings: Settings) -> list[str] | None:
    """Слова, яких немає в словниках, або None, якщо hunspell недоступний."""
    binary = shutil.which("hunspell")
    if binary is None or not words:
        return None
    try:
        proc = subprocess.run(
            [binary, "-d", settings.hunspell_dictionaries, "-l", "-i", "utf-8"],
            input="\n".join(words).encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("hunspell failed", exc_info=True)
        return None
    if proc.returncode != 0 and not proc.stdout:
        logger.warning("hunspell error: %s", proc.stderr[-300:])
        return None
    return [w for w in proc.stdout.decode("utf-8", "ignore").split() if w]


def check_spelling(doc: ParsedDocument, settings: Settings, issues: IssueCollector) -> None:
    if not settings.check_spelling:
        return
    text = URL_RE.sub(" ", normalize(doc.body_text))
    words = [w for w in _WORD_RE.findall(text) if len(w) >= 3 and w[0].islower() and not _is_jargon(w)]
    if len(words) < 40:
        return
    misspelled = find_misspelled(words, settings)
    if misspelled is None:
        return
    share = len(misspelled) / len(words)
    if len(misspelled) >= _MIN_MISSPELLED and share >= settings.spelling_error_share:
        issues.add(
            "too_many_spelling_errors",
            f"Слів з помилками ≈{share:.0%} ({len(misspelled)} з {len(words)}), наприклад: {quote_list(misspelled, 8)}",
        )
