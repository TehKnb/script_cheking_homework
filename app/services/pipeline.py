"""Оркестрація перевірки документа: від байтів файлу до фінального рішення."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import PurePath

from app.checkers.calculations import check_calculations
from app.checkers.classification import check_type_consistency, classify_work_type
from app.checkers.content import (
    check_broken_characters,
    check_currency,
    check_punctuation_spacing,
    check_short_content,
)
from app.checkers.formatting import check_formatting
from app.checkers.funnel import check_funnels
from app.checkers.header import HeaderParseResult, check_header, check_header_consistency, parse_header
from app.checkers.links import check_links
from app.checkers.spelling import check_spelling
from app.checkers.structure import check_dz, check_hypothesis, check_strategic_question
from app.config import Settings
from app.issues import IssueCollector
from app.models import Area, CheckResult, ExtractedData, Routing, Status, WorkType
from app.ocr.engine import OcrEngine, get_ocr_engine
from app.services import converter
from app.services.converter import ConversionError, ConverterUnavailableError, FileKind, detect_kind
from app.services.document_loader import DocumentReadError, ParsedDocument, load_docx
from app.utils.text import normalize
from app.visual.pdf import PdfPage, read_pdf

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".doc", ".docx"}
# Типові розширення, які студенти надсилають замість Word-документа
_KNOWN_EXTENSIONS = SUPPORTED_EXTENSIONS | {
    ".pdf", ".rtf", ".pages", ".odt", ".wps", ".txt", ".jpg", ".jpeg", ".png", ".heic", ".xlsx", ".xls", ".zip",
}

PdfRenderer = Callable[[bytes, Settings], bytes]


class DocumentChecker:
    def __init__(
        self,
        settings: Settings,
        ocr_provider: Callable[[Settings], OcrEngine | None] = get_ocr_engine,
        pdf_renderer: PdfRenderer | None = None,
    ) -> None:
        self._settings = settings
        self._ocr_provider = ocr_provider
        self._pdf_renderer = pdf_renderer or converter.render_pdf

    def check(self, data: bytes, file_name: str) -> CheckResult:
        issues = IssueCollector()
        file_header = parse_header(_file_stem(file_name), allow_underscore=True)
        try:
            return self._check(data, file_name, file_header, issues)
        except Exception:
            logger.exception("Unexpected error while checking %r", file_name)
            issues.add("processing_error")
            return _finalize(WorkType.UNKNOWN, issues, _extracted(file_header, None))

    # ------------------------------------------------------------------ steps

    def _check(
        self, data: bytes, file_name: str, file_header: HeaderParseResult, issues: IssueCollector
    ) -> CheckResult:
        settings = self._settings
        extension = PurePath(file_name).suffix.lower()
        kind = detect_kind(data) if data else FileKind.UNKNOWN

        # 1. Формат і читання файлу
        if data and extension in _KNOWN_EXTENSIONS - SUPPORTED_EXTENSIONS:
            issues.add("unsupported_file_format", f"Отримано файл «{extension}»")
            return _finalize_from_file_name(file_header, issues)
        if kind == FileKind.UNKNOWN:
            if data and extension not in SUPPORTED_EXTENSIONS:
                issues.add("unsupported_file_format", f"Отримано файл «{extension or 'без розширення'}»")
            else:
                issues.add("unreadable_file")
            return _finalize_from_file_name(file_header, issues)

        docx_data = self._to_docx(data, kind, issues)
        if docx_data is None:
            return _finalize_from_file_name(file_header, issues)
        doc = self._load(docx_data, issues)
        if doc is None:
            return _finalize_from_file_name(file_header, issues)

        # 2. Перший рядок і тип роботи
        first = doc.first_paragraph
        text_header = (
            parse_header(first.text, bold_positions=_bold_positions(first)) if first else HeaderParseResult("")
        )
        work_type = classify_work_type(file_header, text_header, doc.body_text)
        if first is None:
            issues.add("empty_document")
        if work_type == WorkType.UNKNOWN:
            issues.add("unclear_work_type")
        dz_number = text_header.dz_number or file_header.dz_number
        is_club = text_header.is_club or file_header.is_club

        # 3. Шапка: назва файлу та перший рядок
        check_header(file_header, work_type, Area.FILE_NAME, settings, issues)
        if first is not None:
            check_header(text_header, work_type, Area.STRUCTURE, settings, issues)
            check_header_consistency(file_header, text_header, issues)

        # 4. Оформлення та текст
        check_formatting(doc, text_header if first else None, settings, issues, work_type)
        check_currency(doc, issues, work_type, dz_number)
        check_calculations(doc, issues)
        check_punctuation_spacing(doc, issues)
        check_broken_characters(doc, issues)
        check_type_consistency(work_type, doc.body_text, issues)
        if settings.check_short_content:
            check_short_content(doc, work_type, dz_number, issues)

        # 5. Структура за типом роботи
        if work_type == WorkType.HYPOTHESIS:
            check_hypothesis(doc, is_club, settings, issues)
        elif work_type == WorkType.STRATEGIC_QUESTION:
            check_strategic_question(doc, is_club, settings, issues)

        # 6. Посилання, воронки, читабельність
        links = check_links(doc, settings, issues)
        pdf_pages = self._render(docx_data, doc)
        ocr = self._ocr_provider(settings) if any(img.is_raster for img in doc.images) else None
        funnels = check_funnels(
            doc,
            ocr,
            pdf_pages,
            settings,
            issues,
            check_numbers=work_type.value in settings.number_format_work_types,
            require_values=work_type in (WorkType.HYPOTHESIS, WorkType.STRATEGIC_QUESTION),
        )
        if work_type == WorkType.DZ:
            # «Відсутня» — лише коли в документі взагалі немає графіки (текстову воронку вже відмічено окремо);
            # графіка є, але воронку не розпізнано — хай подивиться людина
            has_graphics = bool(doc.images) or doc.has_vector_drawings
            if dz_number == "2" and not funnels.has_funnel and has_graphics and not issues.has("funnel_not_visual"):
                issues.add("funnel_unreadable", "ДЗ№2: у документі є графіка, але воронку автоматично не знайдено")
            check_dz(doc, dz_number, funnels.has_funnel or has_graphics or issues.has("funnel_not_visual"), links, issues)

        # 7. Орфографія
        check_spelling(doc, settings, issues)

        # 8. Формат файлу (вимога ранньої версії ТЗ, за замовчуванням вимкнена)
        if settings.docx_requires_funnel and kind == FileKind.DOCX and not funnels.has_funnel:
            issues.add("wrong_file_format")

        return _finalize(work_type, issues, _extracted(file_header, text_header))

    def _to_docx(self, data: bytes, kind: FileKind, issues: IssueCollector) -> bytes | None:
        if kind == FileKind.DOCX:
            return data
        try:
            return converter.convert_to_docx(data, ".doc" if kind == FileKind.DOC else ".rtf", self._settings)
        except ConverterUnavailableError:
            logger.error("LibreOffice is not available; cannot convert .doc")
            issues.add("conversion_unavailable")
        except ConversionError as exc:
            issues.add("unreadable_file", str(exc))
        return None

    def _load(self, docx_data: bytes, issues: IssueCollector) -> ParsedDocument | None:
        try:
            return load_docx(docx_data, int(self._settings.max_unzipped_size_mb * 1024 * 1024))
        except DocumentReadError as exc:
            logger.info("Document is unreadable: %s", exc)
            issues.add("unreadable_file")
            return None

    def _render(self, docx_data: bytes, doc: ParsedDocument) -> list[PdfPage] | None:
        """PDF потрібен лише для документів із фігурами/SmartArt."""
        if not self._settings.render_pdf or not doc.has_vector_drawings:
            return None
        try:
            return read_pdf(self._pdf_renderer(docx_data, self._settings))
        except Exception:
            logger.warning("PDF render failed", exc_info=True)
            return None


# ------------------------------------------------------------------ helpers


def _bold_positions(line) -> set[int]:
    return {run.start + i for run in line.runs if run.bold for i in range(len(run.text))}


def _file_stem(file_name: str) -> str:
    name = PurePath(normalize(file_name)).name
    path = PurePath(name)
    return path.stem if path.suffix.lower() in _KNOWN_EXTENSIONS else name


def _extracted(file_header: HeaderParseResult, text_header: HeaderParseResult | None) -> ExtractedData:
    """Дані з першого рядка документа; якщо там поля немає — з назви файлу."""

    def pick(attr: str) -> str:
        if text_header is not None and text_header.value(attr):
            return text_header.value(attr)
        return file_header.value(attr)

    return ExtractedData(
        student_name=pick("student_name"),
        business=pick("business"),
        city=pick("city"),
        package=pick("package"),
        flow_number=pick("flow_number"),
    )


def _finalize_from_file_name(file_header: HeaderParseResult, issues: IssueCollector) -> CheckResult:
    """Документ не прочитано — тип роботи і дані беремо лише з назви файлу."""
    work_type = classify_work_type(file_header, HeaderParseResult(""))
    if work_type == WorkType.UNKNOWN:
        issues.add("unclear_work_type")
    return _finalize(work_type, issues, _extracted(file_header, None))


def _finalize(work_type: WorkType, issues: IssueCollector, extracted: ExtractedData) -> CheckResult:
    if issues.requires_manual_review:
        status, routing = Status.MANUAL_REVIEW, Routing.MANUAL_REVIEW
    elif len(issues):
        status, routing = Status.NEEDS_REVISION, Routing.RETURN_TO_STUDENT
    else:
        status, routing = Status.PASSED, Routing.SEND_TO_EXPERTS
    return CheckResult(
        status=status,
        work_type=work_type,
        routing=routing,
        issues=issues.issues,
        extracted_data=extracted,
    )
