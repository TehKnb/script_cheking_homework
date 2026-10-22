from __future__ import annotations

import dataclasses

import pytest

from app.config import Settings
from app.models import CheckResult
from app.services import converter
from app.services.converter import OLE_MAGIC
from app.services.pipeline import DocumentChecker
from tests.builders import DocSpec, build_docx

DZ_FILE = "Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №3.doc"
HYPOTHESIS_FILE = "Іваненко Іван – Кав'ярня – Київ – ВІП67 – Гіпотеза.doc"


def make_settings(**overrides) -> Settings:
    """Налаштування для тестів: без LibreOffice, OCR, мережі та hunspell."""
    defaults = dict(
        ocr_engine="none",
        libreoffice_path=None,
        check_links=False,
        check_spelling=False,
        render_pdf=False,
        check_short_content=False,
        api_key="",
    )
    defaults.update(overrides)
    return dataclasses.replace(Settings(), **defaults)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def run_check(settings, monkeypatch):
    """Перевіряє документ так, ніби студент надіслав .doc (конвертація підмінена) або .docx."""

    def _run(spec: DocSpec, file_name: str = DZ_FILE, ocr=None, as_doc: bool | None = None, **overrides) -> CheckResult:
        docx_bytes = build_docx(spec)
        is_doc = file_name.lower().endswith(".doc") if as_doc is None else as_doc
        if is_doc:
            monkeypatch.setattr(converter, "convert_to_docx", lambda data, suffix, s: docx_bytes)
            data = OLE_MAGIC + b"\x00" * 512
        else:
            data = docx_bytes
        effective = dataclasses.replace(settings, **overrides) if overrides else settings
        checker = DocumentChecker(effective, ocr_provider=lambda s: ocr)
        return checker.check(data, file_name)

    return _run


def codes(result: CheckResult) -> set[str]:
    return {issue.code for issue in result.issues}
