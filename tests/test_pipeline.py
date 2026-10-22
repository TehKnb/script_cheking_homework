"""Наскрізні сценарії перевірки документа."""

from app.models import Routing, Status, WorkType
from app.services import converter
from app.services.converter import ConverterUnavailableError, OLE_MAGIC
from app.services.pipeline import DocumentChecker
from tests.builders import (
    BLUE,
    DARK_BLUE,
    DocSpec,
    FakeOcr,
    Part,
    blank_image,
    build_docx,
    funnel_image,
    funnel_tokens,
    header_parts,
    plain,
)
from tests.conftest import DZ_FILE, HYPOTHESIS_FILE, codes, make_settings

HYPOTHESIS_BODY = plain(
    "Моя точка А (потік) – 3 000 $",
    "Планована т.Б – 5 000 $",
    "Ціна питання – 24 000 $",
    "Моя гіпотеза полягає в тому, що запуск доставки кави в офіси дасть +50 замовлень на тиждень.",
    "Мої питання: чи варто тестувати Instagram, чи краще одразу домовлятися з бізнес-центрами?",
)
CLUB_BODY = plain(
    "Моя точка А (потік) – 3 000 $",
    "Точка Б (після базової 7-ми тижневої програми) – 5 000 $",
    "Точка Б (клуб, липень) – 10 000 $",
    "За рахунок чого відбулось зростання: запустили рекламу на нову послугу.",
    "Планована т.Б – 13 000 $",
    "Ціна питання – 36 000 $",
    "Моя гіпотеза полягає в тому, що збільшення рекламного бюджету дасть більше звернень.",
    "Питання: як вплине збільшення бюджету на продажі?",
)
CLUB_HYPOTHESIS_FILE = "Іваненко Іван – Кав'ярня – Київ – Річна63 – Гіпотеза.doc"


def test_clean_dz_passes(run_check):
    result = run_check(DocSpec(header_parts(), plain("Виконав завдання. Охоплення 10 000 людей, бюджет 120 $.")))
    assert result.issues == []
    assert result.status == Status.PASSED
    assert result.routing == Routing.SEND_TO_EXPERTS
    assert result.work_type == WorkType.DZ
    assert result.extracted_data.model_dump() == {
        "student_name": "Іваненко Іван",
        "business": "Кав'ярня",
        "city": "Київ",
        "package": "ВІП",
        "flow_number": "67",
    }


def test_formatting_errors(run_check):
    spec = DocSpec(
        header_parts(package_color=BLUE),
        [
            [Part("Текст шрифтом Arial, якого тут занадто багато для допуску", font="Arial")],
            [Part("Дрібний текст", size=9)],
            [Part("Великий текст", size=14)],
        ],
        first_size=16,
    )
    assert codes(run_check(spec)) == {
        "wrong_font",
        "wrong_first_line_size",
        "wrong_body_text_size",
        "wrong_package_color",
    }


def test_body_text_11pt_is_tolerated(run_check):
    assert run_check(DocSpec(header_parts(), [[Part("Звіт по рекламі", size=11)]])).issues == []


def test_theme_font_from_default_template_is_detected(run_check):
    spec = DocSpec(header_parts(), [[Part("Текст без явного шрифту, набраний шрифтом теми документа", font=None)]])
    assert "wrong_font" in codes(run_check(spec))


def test_flow_number_must_be_bold_and_colored(run_check):
    parts = header_parts()
    parts[3:4] = [Part("ВІП", bold=True, color=BLUE.__class__(0xFF, 0, 0)), Part("67")]
    issue_codes = codes(run_check(DocSpec(parts)))
    assert {"wrong_bold_formatting", "wrong_package_color"} <= issue_codes


def test_standard_package_blue_shades(run_check):
    file_name = "Іваненко Іван – Кав'ярня – Київ – Стандарт67 – ДЗ №3.doc"
    for color in (BLUE, DARK_BLUE):
        ok = run_check(DocSpec(header_parts(package="Стандарт67", package_color=color)), file_name)
        assert "wrong_package_color" not in codes(ok)
    bad = run_check(DocSpec(header_parts(package="Стандарт67", package_color=None)), file_name)
    assert "wrong_package_color" in codes(bad)


def test_annual_package_may_be_black_or_red(run_check):
    file_name = "Іваненко Іван – Кав'ярня – Київ – Річна63 – ДЗ №3.doc"
    for color in (None, BLUE.__class__(0xFF, 0, 0)):
        result = run_check(DocSpec(header_parts(package="Річна63", package_color=color)), file_name)
        assert "wrong_package_color" not in codes(result)


def test_file_name_mismatch(run_check):
    result = run_check(DocSpec(header_parts()), "Іваненко Іван – Кав'ярня – Одеса – ВІП67 – ДЗ №3.doc")
    assert "header_mismatch" in codes(result)


def test_unclear_work_type_goes_to_manual_review(run_check):
    spec = DocSpec([Part("Іваненко Іван – Кав'ярня – Київ – "), Part("ВІП67", bold=True)])
    result = run_check(spec, "Іваненко Іван.doc")
    assert result.work_type == WorkType.UNKNOWN
    assert "unclear_work_type" in codes(result)
    assert result.status == Status.MANUAL_REVIEW


def test_landscape_orientation(run_check):
    assert "landscape_orientation" in codes(run_check(DocSpec(header_parts(), landscape=True)))


def test_currency(run_check):
    body = plain("Дохід 20 000 грн", "Витрати 5 000 грн", "Прибуток 15 000 грн", "Реклама 100 $")
    dz1 = "Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №1.doc"
    assert "wrong_currency" in codes(run_check(DocSpec(header_parts(marker="ДЗ №1"), body), dz1))
    # ДЗ№3 (найм) — зарплати в гривнях допускаються
    assert "wrong_currency" not in codes(run_check(DocSpec(header_parts(marker="ДЗ №3"), body), DZ_FILE))
    # Поодинокі згадки гривні допускаються
    few = plain("Бронювання коштує 35 000 грн за людину", "Дохід 9 600 $", "Витрати 2 000 $")
    assert "wrong_currency" not in codes(run_check(DocSpec(header_parts(marker="ДЗ №1"), few), dz1))


def test_missing_space_after_punctuation(run_check):
    body = plain("Клієнти задоволені,рейтинги високі.пацієнти радіють,персонал працює,все добре.дякую")
    assert "missing_space_after_punctuation" in codes(run_check(DocSpec(header_parts(), body)))


def test_short_content_goes_to_curator(run_check):
    result = run_check(DocSpec(header_parts(), plain("Зробив.")), check_short_content=True)
    assert "short_content" in codes(result)
    assert result.status == Status.MANUAL_REVIEW


# ------------------------------------------------------------------ hypothesis


def test_hypothesis_passes(run_check):
    spec = DocSpec(header_parts(marker="Гіпотеза"), HYPOTHESIS_BODY)
    result = run_check(spec, HYPOTHESIS_FILE)
    assert result.work_type == WorkType.HYPOTHESIS
    assert result.issues == []


def test_club_hypothesis_uses_latest_point_for_question_value(run_check):
    spec = DocSpec(header_parts(package="Річна63", marker="Гіпотеза"), CLUB_BODY)
    assert run_check(spec, CLUB_HYPOTHESIS_FILE).issues == []


def test_club_hypothesis_requires_club_metrics(run_check):
    spec = DocSpec(header_parts(package="Річна63", marker="Гіпотеза"), HYPOTHESIS_BODY)
    assert {"missing_point_b_club", "missing_growth_reason"} <= codes(run_check(spec, CLUB_HYPOTHESIS_FILE))


def test_hypothesis_wrong_calculation(run_check):
    body = list(HYPOTHESIS_BODY)
    body[2] = [Part("Ціна питання – 30 000 $")]
    result = run_check(DocSpec(header_parts(marker="Гіпотеза"), body), HYPOTHESIS_FILE)
    issue = next(i for i in result.issues if i.code == "wrong_question_value_calculation")
    assert issue.area.value == "calculation"
    assert "24 000" in issue.description


def test_hypothesis_negative_point_a(run_check):
    body = list(HYPOTHESIS_BODY)
    body[0] = [Part("Моя т.А - -2 100$")]
    body[1] = [Part("Запланована т.Б. - 8 000$")]
    body[2] = [Part("Ціна питання: 121 200$")]
    assert run_check(DocSpec(header_parts(marker="Гіпотеза"), body), HYPOTHESIS_FILE).issues == []


def test_hypothesis_missing_blocks_and_currency(run_check):
    body = [[Part("Моя точка А (потік) – 3 000")]] + HYPOTHESIS_BODY[3:]
    result = run_check(DocSpec(header_parts(marker="Гіпотеза"), body), HYPOTHESIS_FILE)
    assert {"missing_point_b", "missing_question_value", "missing_currency"} <= codes(result)


def test_hypothesis_latin_lookalike_labels(run_check):
    body = list(HYPOTHESIS_BODY)
    body[0] = [Part("Моя точка A (latin) – 3 000 $")]
    assert "missing_point_a" not in codes(run_check(DocSpec(header_parts(marker="Гіпотеза"), body), HYPOTHESIS_FILE))


def test_hypothesis_named_but_strategic_question_inside(run_check):
    body = HYPOTHESIS_BODY + plain("Моє питання стратегічне, оскільки від нього залежить розвиток.")
    result = run_check(DocSpec(header_parts(marker="Гіпотеза"), body), HYPOTHESIS_FILE)
    assert "work_type_mismatch" in codes(result)


# ------------------------------------------------------------ strategic question


SQ_FILE = "Іваненко Іван – Кав'ярня – Київ – ВІП67 – Стратегічне питання.doc"


def test_strategic_question_passes(run_check):
    body = plain(
        "Моя точка А – 10 000 $",
        "1. Моє питання стратегічне, оскільки від нього залежить розвиток бізнесу на рік.",
        "2. Суть питання: чи відкривати другу кав'ярню, чи розвивати доставку.",
        "3. Мої варіанти: відкрити точку в центрі або запустити доставку в офіси.",
    )
    assert run_check(DocSpec(header_parts(marker="Стратегічне питання"), body), SQ_FILE).issues == []


def test_strategic_question_missing_parts(run_check):
    body = plain("Я хочу розвивати бізнес і заробляти більше грошей у наступному році, працюю над цим щодня.")
    result = run_check(DocSpec(header_parts(marker="Стратегічне питання"), body), SQ_FILE)
    assert {"missing_point_a", "missing_strategic_reason", "missing_solution_options"} <= codes(result)


# ------------------------------------------------------------------ DZ specifics


DZ2_FILE = "Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №2.doc"


def test_dz2_requires_point_a_and_visual_funnel(run_check):
    body = plain(
        "1. Опишіть у ворді вашу точку А.",
        "Охоплення локальної аудиторії 3 000 —",
        "Переходи в профіль 300 10% від охоплення",
        "Звернення 45 15% від переходів",
        "Оплата 15 33% від звернень",
    )
    result = run_check(DocSpec(header_parts(marker="ДЗ №2"), body), DZ2_FILE)
    assert {"missing_point_a", "funnel_not_visual"} <= codes(result)
    assert "missing_funnel" not in codes(result)


def test_dz2_without_any_funnel(run_check):
    body = plain("Точка А: 20 клієнтів × 150 $ = 3 000 $", "Решта завдань виконана текстом без воронки.")
    result = run_check(DocSpec(header_parts(marker="ДЗ №2"), body), DZ2_FILE)
    assert codes(result) == {"missing_funnel"}


def test_dz7_requires_landing_link(run_check):
    file_name = "Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №7.doc"
    assert "missing_landing_link" in codes(run_check(DocSpec(header_parts(marker="ДЗ №7")), file_name))
    ok = run_check(DocSpec(header_parts(marker="ДЗ №7"), hyperlink="https://abc.creatium.work/"), file_name)
    assert ok.issues == []


# ------------------------------------------------------------------ links


def test_links(run_check):
    not_clickable = run_check(DocSpec(header_parts(), plain("Креативи тут: https://drive.google.com/drive/folders/123")))
    assert "link_not_clickable" in codes(not_clickable)
    editor = run_check(DocSpec(header_parts(), hyperlink="https://my.creatium.app/524309"))
    assert "landing_editor_link" in codes(editor)
    private = run_check(DocSpec(header_parts(), hyperlink="https://adsmanager.facebook.com/adsmanager/manage"))
    assert "link_requires_access" in codes(private)
    fine = run_check(DocSpec(header_parts(), hyperlink="https://www.instagram.com/my.cafe/"))
    assert fine.issues == []


# ------------------------------------------------------------------ funnels / format


def test_docx_without_funnel_is_fine_by_default(run_check):
    assert run_check(DocSpec(header_parts()), DZ_FILE + "x").issues == []


def test_docx_without_funnel_with_legacy_rule(run_check):
    result = run_check(DocSpec(header_parts()), DZ_FILE + "x", docx_requires_funnel=True)
    assert codes(result) == {"wrong_file_format"}


def test_docx_with_correct_funnel_passes(run_check):
    ocr = FakeOcr(funnel_tokens())
    result = run_check(DocSpec(header_parts(), images=[funnel_image()]), DZ_FILE + "x", ocr=ocr)
    assert ocr.calls >= 1
    assert result.issues == []


def test_non_funnel_image_is_ignored(run_check):
    result = run_check(DocSpec(header_parts(), images=[blank_image()]), DZ_FILE + "x", ocr=FakeOcr([]))
    assert result.issues == []


def test_funnel_errors(run_check):
    ocr = FakeOcr(funnel_tokens(conversions=("25%", None), labels=("Охоплення", None, "Продажі")))
    result = run_check(DocSpec(header_parts(), images=[funnel_image()]), DZ_FILE + "x", ocr=ocr)
    assert codes(result) == {"wrong_funnel_conversion", "missing_funnel_conversion", "missing_funnel_stage_label"}
    wrong = next(i for i in result.issues if i.code == "wrong_funnel_conversion")
    assert "вказано 25%, правильно 20%" in wrong.description


def test_images_without_ocr_go_to_manual_review(run_check):
    result = run_check(DocSpec(header_parts(), images=[funnel_image()]), DZ_FILE, ocr=None)
    assert codes(result) == {"funnel_check_unavailable"}
    assert result.status == Status.MANUAL_REVIEW


# ------------------------------------------------------------------ broken input


def test_corrupted_docx_is_unreadable(settings):
    result = DocumentChecker(settings).check(b"PK\x03\x04garbage", DZ_FILE + "x")
    assert codes(result) == {"unreadable_file"}
    assert result.status == Status.MANUAL_REVIEW
    assert result.work_type == WorkType.DZ  # тип визначено за назвою файлу
    assert result.extracted_data.city == "Київ"


def test_empty_file_is_unreadable(settings):
    result = DocumentChecker(settings).check(b"", DZ_FILE)
    assert "unreadable_file" in codes(result)
    assert result.routing == Routing.MANUAL_REVIEW


def test_pdf_and_other_formats_are_unsupported(settings):
    for ext, data in ((".pdf", b"%PDF-1.7 ..."), (".pages", b"PK\x03\x04..."), (".rtf", b"{\\rtf1 ...")):
        result = DocumentChecker(settings).check(data, f"Іваненко Іван – Кафе – Київ – ВІП67 – ДЗ №1{ext}")
        assert codes(result) == {"unsupported_file_format"}, ext
        assert result.status == Status.NEEDS_REVISION


def test_doc_without_libreoffice_goes_to_manual_review(settings, monkeypatch):
    def unavailable(*_):
        raise ConverterUnavailableError("no soffice")

    monkeypatch.setattr(converter, "convert_to_docx", unavailable)
    result = DocumentChecker(settings).check(OLE_MAGIC + b"\x00" * 100, DZ_FILE)
    assert codes(result) == {"conversion_unavailable"}
    assert result.status == Status.MANUAL_REVIEW


def test_internal_error_is_contained(monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "check_formatting", lambda *a, **k: 1 / 0)
    result = DocumentChecker(make_settings()).check(build_docx(DocSpec(header_parts())), DZ_FILE + "x")
    assert codes(result) == {"processing_error"}
    assert result.status == Status.MANUAL_REVIEW
