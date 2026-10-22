"""Абстракція над OCR-рушіями (EasyOCR / Tesseract).

Рушій створюється ліниво і один раз на процес: ініціалізація EasyOCR займає секунди
і завантажує моделі в пам'ять.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

import numpy as np

from app.config import Settings
from app.visual.layout import Token

logger = logging.getLogger(__name__)

# Відповідність мовних кодів EasyOCR -> Tesseract
_TESSERACT_LANGS = {"uk": "ukr", "en": "eng", "ru": "rus"}

# OCR повертає ті самі токени, що й розбір PDF: текст + координати + впевненість
OcrToken = Token


class OcrEngine(Protocol):
    name: str

    def read(self, image: np.ndarray) -> list[OcrToken]:
        """Весь текст зображення з координатами. image — BGR або grayscale масив OpenCV."""
        ...

    def read_region(self, image: np.ndarray, single_line: bool, digits: bool = False) -> str:
        """Текст невеликого фрагмента (вміст блоку воронки, підпис, конверсія).
        Розпізнавання по фрагментах значно точніше, ніж по всьому зображенню."""
        ...


class EasyOcrEngine:
    name = "easyocr"

    def __init__(self, languages: tuple[str, ...], gpu: bool) -> None:
        import easyocr  # важкий імпорт (torch) — лише за потреби

        self._reader = easyocr.Reader(list(languages), gpu=gpu, verbose=False)
        self._lock = threading.Lock()  # Reader не гарантує потокобезпечність

    def read(self, image: np.ndarray) -> list[OcrToken]:
        with self._lock:
            raw = self._reader.readtext(image, detail=1, paragraph=False)
        tokens: list[OcrToken] = []
        for box, text, conf in raw:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            tokens.append(OcrToken(str(text), min(xs), min(ys), max(xs), max(ys), float(conf)))
        return tokens

    def read_region(self, image: np.ndarray, single_line: bool, digits: bool = False) -> str:
        with self._lock:
            parts = self._reader.readtext(
                image, detail=0, paragraph=not single_line, allowlist="0123456789,.% " if digits else None
            )
        return " ".join(str(p) for p in parts).strip()


class TesseractEngine:
    name = "tesseract"

    def __init__(self, languages: tuple[str, ...], tesseract_cmd: str | None) -> None:
        import pytesseract

        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        pytesseract.get_tesseract_version()  # кидає виняток, якщо бінарника немає
        self._pytesseract = pytesseract
        self._lang = "+".join(_TESSERACT_LANGS.get(l.strip(), l.strip()) for l in languages)

    def read(self, image: np.ndarray) -> list[OcrToken]:
        data = self._pytesseract.image_to_data(
            image, lang=self._lang, config="--psm 11", output_type=self._pytesseract.Output.DICT
        )
        tokens: list[OcrToken] = []
        for i, text in enumerate(data["text"]):
            text = (text or "").strip()
            conf = float(data["conf"][i])
            if not text or conf < 0:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            tokens.append(OcrToken(text, x, y, x + w, y + h, conf / 100))
        return tokens

    def read_region(self, image: np.ndarray, single_line: bool, digits: bool = False) -> str:
        config = "--psm 7" if single_line else "--psm 6"
        if digits:
            config += " -c tessedit_char_whitelist=0123456789,.%"
        text = self._pytesseract.image_to_string(image, lang=self._lang, config=config)
        return " ".join(text.split())


_engine_lock = threading.Lock()
_engine_cache: dict[str, OcrEngine | None] = {}


def get_ocr_engine(settings: Settings) -> OcrEngine | None:
    """Повертає налаштований OCR-рушій або None, якщо жоден недоступний."""
    key = settings.ocr_engine
    with _engine_lock:
        if key in _engine_cache:
            return _engine_cache[key]
        engine = _create_engine(settings)
        _engine_cache[key] = engine
        if engine is None:
            logger.warning("OCR engine is not available (OCR_ENGINE=%s); funnels will go to manual review", key)
        else:
            logger.info("OCR engine initialised: %s", engine.name)
        return engine


def _create_engine(settings: Settings) -> OcrEngine | None:
    order = {
        "auto": ("easyocr", "tesseract"),
        "easyocr": ("easyocr",),
        "tesseract": ("tesseract",),
    }.get(settings.ocr_engine, ())
    for name in order:
        try:
            if name == "easyocr":
                return EasyOcrEngine(settings.ocr_languages, settings.ocr_gpu)
            return TesseractEngine(settings.ocr_languages, settings.tesseract_cmd)
        except Exception as exc:  # бібліотека/модель/бінарник відсутні — пробуємо наступний
            logger.info("OCR engine %s unavailable: %s", name, exc)
    return None
