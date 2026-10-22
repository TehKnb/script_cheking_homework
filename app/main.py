"""FastAPI-застосунок: POST /check-document."""

from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.security import APIKeyHeader

from app import __version__
from app.config import Settings, get_settings
from app.models import CheckResult
from app.ocr.engine import get_ocr_engine
from app.services.pipeline import DocumentChecker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dz_checker")

_READ_CHUNK = 1024 * 1024
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if not settings.api_key:
        logger.warning("API_KEY is not set: /check-document is open to anyone who can reach the service")
    if settings.ocr_engine != "none":
        # Ініціалізація OCR (завантаження моделей) у фоні, щоб перший запит не чекав
        threading.Thread(target=get_ocr_engine, args=(settings,), daemon=True).start()
    yield


app = FastAPI(
    title="DZ Checker",
    description="Первинне сортування та перевірка формальних критеріїв навчальних робіт (.doc / .docx)",
    version=__version__,
    lifespan=lifespan,
)


def get_checker(settings: Settings = Depends(get_settings)) -> DocumentChecker:
    return DocumentChecker(settings)


def require_api_key(
    api_key: str | None = Security(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        return
    if not api_key or not secrets.compare_digest(api_key.encode(), settings.api_key.encode()):
        raise HTTPException(
            status_code=401,
            detail="Невірний або відсутній API-ключ (заголовок X-API-Key)",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    buffer = bytearray()
    while chunk := upload.file.read(_READ_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Файл перевищує допустимий розмір {max_bytes // (1024 * 1024)} МБ",
            )
    return bytes(buffer)


@app.post("/check-document", response_model=CheckResult, dependencies=[Depends(require_api_key)])
def check_document(
    file: UploadFile = File(..., description="Файл роботи (.doc або .docx)"),
    file_name: str = Form(..., description="Оригінальна назва файлу"),
    settings: Settings = Depends(get_settings),
    checker: DocumentChecker = Depends(get_checker),
) -> CheckResult:
    # Синхронний обробник: FastAPI виконує його в пулі потоків, тож OCR і LibreOffice не блокують event loop
    data = _read_limited(file, int(settings.max_file_size_mb * 1024 * 1024))
    name = file_name.strip() or (file.filename or "")
    result = checker.check(data, name)
    logger.info(
        "Checked %r: status=%s type=%s issues=%s",
        name,
        result.status.value,
        result.work_type.value,
        [i.code for i in result.issues],
    )
    return result


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "libreoffice": bool(settings.libreoffice_path),
        "ocr_engine_setting": settings.ocr_engine,
        "auth_enabled": bool(settings.api_key),
    }
