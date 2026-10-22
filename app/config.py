"""Налаштування сервісу. Усі значення можна перевизначити змінними оточення."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


def _detect_libreoffice() -> str | None:
    explicit = os.getenv("LIBREOFFICE_PATH")
    if explicit:
        return explicit
    for candidate in ("soffice", "libreoffice"):
        found = shutil.which(candidate)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/lib/libreoffice/program/soffice",
    ):
        if Path(candidate).exists():
            return candidate
    return None


@dataclass(frozen=True)
class Settings:
    # --- Доступ ---
    # Якщо задано, POST /check-document вимагає заголовок X-API-Key з цим значенням
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", "").strip())

    # --- Конвертація .doc -> .docx ---
    libreoffice_path: str | None = field(default_factory=_detect_libreoffice)
    conversion_timeout_sec: float = field(default_factory=lambda: _env_float("CONVERSION_TIMEOUT_SEC", 90))

    # --- Обмеження на вхідний файл ---
    max_file_size_mb: float = field(default_factory=lambda: _env_float("MAX_FILE_SIZE_MB", 25))
    # Захист від zip-бомб: сумарний розпакований розмір .docx
    max_unzipped_size_mb: float = field(default_factory=lambda: _env_float("MAX_UNZIPPED_SIZE_MB", 300))

    # --- Оформлення ---
    required_font: str = field(default_factory=lambda: os.getenv("REQUIRED_FONT", "Times New Roman"))
    first_line_size_pt: float = field(default_factory=lambda: _env_float("FIRST_LINE_SIZE_PT", 14))
    body_size_pt: float = field(default_factory=lambda: _env_float("BODY_SIZE_PT", 12))
    # Наскільки основний текст може бути дрібнішим (11 pt трапляється в прийнятих роботах)
    body_size_tolerance_pt: float = field(default_factory=lambda: _env_float("BODY_SIZE_TOLERANCE_PT", 1))
    # Роздільник тисяч: у відмовах його вимагають для Гіпотез/Стратегічних питань і для великих сум.
    # У прийнятих ДЗ трапляються «1543», «20592» — тому за замовчуванням ДЗ не перевіряються
    number_format_min_value: int = field(default_factory=lambda: int(_env_float("NUMBER_FORMAT_MIN_VALUE", 10000)))
    number_format_work_types: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip() for t in os.getenv("NUMBER_FORMAT_WORK_TYPES", "hypothesis,strategic_question").split(",")
        )
    )

    # Пакет пишеться разом із номером потоку («ВІП67») — так вимагають у відмовах
    require_flow_number: bool = field(default_factory=lambda: _env_bool("REQUIRE_FLOW_NUMBER", True))
    # Вимога ранньої версії ТЗ: .docx лише для робіт з воронкою. Реальні прийняті роботи її не дотримуються
    docx_requires_funnel: bool = field(default_factory=lambda: _env_bool("DOCX_REQUIRES_FUNNEL", False))

    # Роботи з дуже малим наповненням — на погодження з куратором (manual_review)
    check_short_content: bool = field(default_factory=lambda: _env_bool("CHECK_SHORT_CONTENT", True))

    # --- Посилання ---
    check_links: bool = field(default_factory=lambda: _env_bool("CHECK_LINKS", True))
    link_timeout_sec: float = field(default_factory=lambda: _env_float("LINK_TIMEOUT_SEC", 8))
    max_links_to_check: int = field(default_factory=lambda: int(_env_float("MAX_LINKS_TO_CHECK", 15)))

    # --- Орфографія (hunspell) ---
    check_spelling: bool = field(default_factory=lambda: _env_bool("CHECK_SPELLING", True))
    hunspell_dictionaries: str = field(default_factory=lambda: os.getenv("HUNSPELL_DICTIONARIES", "uk_UA,ru_RU,en_US"))
    # Частка слів з помилками, після якої робота повертається
    spelling_error_share: float = field(default_factory=lambda: _env_float("SPELLING_ERROR_SHARE", 0.07))

    # --- Рендер у PDF (воронки SmartArt/фігурами, читабельність) ---
    render_pdf: bool = field(default_factory=lambda: _env_bool("RENDER_PDF", True))

    # --- Гіпотеза ---
    question_value_multiplier: float = field(default_factory=lambda: _env_float("QUESTION_VALUE_MULTIPLIER", 12))
    question_value_tolerance: float = field(default_factory=lambda: _env_float("QUESTION_VALUE_TOLERANCE", 1))

    # --- Воронка / OCR ---
    # auto | easyocr | tesseract | none
    ocr_engine: str = field(default_factory=lambda: os.getenv("OCR_ENGINE", "auto").strip().lower())
    ocr_languages: tuple[str, ...] = field(
        default_factory=lambda: tuple(os.getenv("OCR_LANGUAGES", "uk,en").split(","))
    )
    ocr_gpu: bool = field(default_factory=lambda: _env_bool("OCR_GPU", False))
    ocr_min_confidence: float = field(default_factory=lambda: _env_float("OCR_MIN_CONFIDENCE", 0.3))
    tesseract_cmd: str | None = field(default_factory=lambda: os.getenv("TESSERACT_CMD"))
    # Додатковий допуск конверсії у п.п. Базовий — менше одиниці останнього знака, який написав студент
    # («20%» для 19,6%, «28%» для 28,6%, «7,6%» для 7,64%), див. is_conversion_correct
    funnel_conversion_tolerance_pp: float = field(
        default_factory=lambda: _env_float("FUNNEL_CONVERSION_TOLERANCE_PP", 0.0)
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
