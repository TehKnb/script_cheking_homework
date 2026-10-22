"""Визначення формату файлу та конвертація .doc -> .docx через LibreOffice CLI."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from enum import Enum
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .doc (Compound File Binary)
ZIP_MAGIC = b"PK\x03\x04"  # .docx (OOXML)
RTF_MAGIC = b"{\\rtf"  # Word часто зберігає RTF з розширенням .doc


class FileKind(str, Enum):
    DOC = "doc"
    DOCX = "docx"
    RTF = "rtf"
    UNKNOWN = "unknown"


class ConversionError(Exception):
    """Файл не вдалося сконвертувати (найчастіше — він пошкоджений)."""


class ConverterUnavailableError(ConversionError):
    """LibreOffice не встановлений або не знайдений."""


def detect_kind(data: bytes) -> FileKind:
    if data.startswith(OLE_MAGIC):
        return FileKind.DOC
    if data.startswith(ZIP_MAGIC):
        return FileKind.DOCX
    if data.lstrip().startswith(RTF_MAGIC):
        return FileKind.RTF
    return FileKind.UNKNOWN


_FILTERS = {"docx": "docx:MS Word 2007 XML", "pdf": "pdf:writer_pdf_Export"}


def convert_to_docx(data: bytes, source_suffix: str, settings: Settings) -> bytes:
    return convert_document(data, source_suffix, "docx", settings)


def render_pdf(docx_data: bytes, settings: Settings) -> bytes:
    """Рендер документа в PDF — як його побачить перевіряючий (SmartArt, фігури, текстові поля)."""
    return convert_document(docx_data, ".docx", "pdf", settings)


def convert_document(data: bytes, source_suffix: str, target: str, settings: Settings) -> bytes:
    """Конвертує документ LibreOffice-ом у формат target (docx | pdf) і повертає байти результату.

    Для кожного виклику створюється окремий профіль LibreOffice, щоб паралельні запити
    не блокували один одного.
    """
    soffice = settings.libreoffice_path
    if not soffice or not (Path(soffice).exists() or shutil.which(soffice)):
        raise ConverterUnavailableError("LibreOffice (soffice) не знайдено")

    with tempfile.TemporaryDirectory(prefix="dzcheck_") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / f"input{source_suffix}"
        src.write_bytes(data)
        out_dir = tmp_dir / "out"
        out_dir.mkdir()
        profile_dir = tmp_dir / f"lo_profile_{uuid.uuid4().hex}"

        cmd = [
            soffice,
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            _FILTERS[target],
            "--outdir",
            str(out_dir),
            str(src),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=settings.conversion_timeout_sec,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ConverterUnavailableError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("Перевищено час очікування конвертації") from exc

        result = out_dir / f"input.{target}"
        if proc.returncode != 0 or not result.exists() or result.stat().st_size == 0:
            logger.warning(
                "LibreOffice conversion failed: rc=%s stdout=%r stderr=%r",
                proc.returncode,
                proc.stdout[-500:],
                proc.stderr[-500:],
            )
            raise ConversionError("LibreOffice не зміг сконвертувати файл")
        return result.read_bytes()
