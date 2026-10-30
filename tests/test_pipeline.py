"""Наскрізні сценарії перевірки документа."""

import re

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
    RED,
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


def test_prices_without_currency(run_check):
    body = plain(
        "Лід магніт товари:",
        "- Комплект інсталяції по 7999",
        "- Дизайнерський радіатор за 6400",
        "Замовляємо по 100 штук, працюємо за 2024 рік, конверсія за 300 %.",
    )
    result = run_check(DocSpec(header_parts(marker="ДЗ №6"), body), "Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №6.doc")
    issue = next(i for i in result.issues if i.code == "missing_currency")
    assert "по 7999" in issue.description and "за 6400" in issue.description
    assert "100" not in issue.description and "2024" not in issue.description and "300" not in issue.description
    ok = plain("- Комплект інсталяції по 7999 $", "- Радіатор за 6400$", "Відкручений бюджет 10 доларів")
    result = run_check(DocSpec(header_parts(marker="ДЗ №6"), ok), "Іваненко Іван – Кав'ярня – Київ – ВІП67 – ДЗ №6.doc")
    assert "missing_currency" not in codes(result)


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


def test_club_requires_both_points_b(run_check):
    def issues_without(line: str) -> list[str]:
        spec = DocSpec(header_parts(package="Річна63", marker="Гіпотеза"), [b for b in CLUB_BODY if line not in b[0].text])
        return [i.description for i in run_check(spec, CLUB_HYPOTHESIS_FILE).issues if i.code == "missing_point_b_club"]

    assert issues_without("після базової") and "після базової програми" in issues_without("після базової")[0]
    assert issues_without("клуб, липень") and "точку Б (клуб)" in issues_without("клуб, липень")[0]


def test_club_point_before_club_counts_as_program_point():
    from app.checkers.structure import extract_metrics
    from app.services.document_loader import ParagraphInfo

    lines = ["т А до клубу 2 400 $", "Точка Б (7 тижнів) - 3500$", "т А в клубі: 12 000$"]
    kinds = [m.kind for m in extract_metrics([ParagraphInfo(i, text, []) for i, text in enumerate(lines)]).lines]
    assert kinds == ["point_b_program", "point_b_program", "point_a_club"]


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


def test_missing_template_blocks_go_to_manual_review(run_check):
    body = [[Part("Моя точка А (потік) – 3 000 $")]] + HYPOTHESIS_BODY[3:]
    result = run_check(DocSpec(header_parts(marker="Гіпотеза"), body), HYPOTHESIS_FILE)
    assert {"missing_point_b", "missing_question_value"} <= codes(result)
    assert result.status == Status.MANUAL_REVIEW and result.routing == Routing.MANUAL_REVIEW


def test_hypothesis_without_type_in_header_and_file_name(run_check):
    spec = DocSpec(header_parts(marker="Гіпотеза")[:-1], HYPOTHESIS_BODY)
    result = run_check(spec, "Іваненко Іван – Кав'ярня – Київ – ВІП67.docx")
    assert result.work_type == WorkType.HYPOTHESIS
    assert result.issues == []


def test_type_without_marker_is_detected_from_loose_content(run_check):
    header = header_parts(marker="Гіпотеза")[:-1]
    file_name = "Іваненко Іван – Кав'ярня – Київ – ВІП67.docx"
    hypothesis = plain("Моя точка А (потік) – 3 000 $", "Хочемо затестувати гіпотезу з доставкою в офіси.")
    question = plain("Моя точка А (потік) – 3 000 $", "Моє питання про зміну мотивації у відділі продажів.")
    unclear = plain("Текст роботи без точок і питань, лише опис бізнесу кав'ярні.")
    for body, expected in ((hypothesis, WorkType.HYPOTHESIS), (question, WorkType.STRATEGIC_QUESTION)):
        result = run_check(DocSpec(header, body), file_name)
        assert result.work_type == expected
        assert "missing_work_type_marker" not in codes(result)
    result = run_check(DocSpec(header, unclear), file_name)
    assert result.status == Status.MANUAL_REVIEW
    assert "unclear_work_type" in codes(result) and "missing_work_type_marker" not in codes(result)


def test_new_packages_in_black_without_flow(run_check):
    # Платинум КМ — річний доступ (як КМ+), тож потрібні клубні показники
    for package, body in (("ПРОРИВ", HYPOTHESIS_BODY), ("Платинум КМ", CLUB_BODY), ("Економ", HYPOTHESIS_BODY)):
        spec = DocSpec(header_parts(package=package, marker="Гіпотеза", package_color=None), body)
        result = run_check(spec, f"Іваненко Іван – Кав'ярня – Київ – {package} – Гіпотеза.docx")
        assert result.issues == [], package


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


def test_strategic_question_with_directions_and_unchecked_price(run_check):
    # «Моє стратегічне питання: …», варіанти як «напрямки», ціна питання без планованої т.Б (у потоці не обов'язкова)
    body = plain(
        "Моя точка А (потік) - 3 000 $",
        "Ціна питання - 36 000 $",
        "Маю два напрямки масштабування:",
        "Перший — масштабувати рекламні зв'язки, які вже приносять ліди.",
        "Другий — розвивати нове позиціонування подарунків на день народження.",
        "Моє стратегічне питання: як розподілити бюджет і час команди між цими напрямками?",
    )
    header = header_parts(package="ПРОРИВ63-3", marker="Стратегічне питання")
    file_name = "Іваненко Іван – Кав'ярня – Київ – ПРОРИВ63-3 – Стратегічне питання (1).docx"
    result = run_check(DocSpec(header, body), file_name)
    assert result.issues == []
    assert result.extracted_data.package == "ПРОРИВ" and result.extracted_data.flow_number == "63"


def test_issue_texts_have_no_telegram_markup(run_check):
    body = plain("Текст роботи.")
    body.append([Part("______________", size=14)])  # лінія для підпису — не текст
    body.append([Part("Рядок 14 розміру з_підкресленням і <тегом> & [дужкою] * `", size=14)])
    result = run_check(DocSpec(header_parts(), body), DZ_FILE)
    text = " ".join(i.description for i in result.issues)
    assert "wrong_body_text_size" in codes(result) and "______" not in text
    assert not set("_*`[]<>&") & set(text)


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
    assert "Охоплення → крок 2: 2 000 ÷ 10 000 × 100% = 20%, а вказано 25%" in wrong.description


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


# --------------------------------------------- шапка у двох абзацах, обрізана назва


def _two_paragraph_header() -> DocSpec:
    # Word розбив довгу шапку на два абзаци; другий — того ж розміру (14 pt)
    first = [Part("Іваненко Іван – "), Part("Юридичний супровід угод та підбір нерухомості", bold=True), Part(", -")]
    second = [Part("м Вишково, Закарпаття – ", size=14), Part("ВІП67", bold=True, color=RED, size=14), Part(" – ДЗ №3", size=14)]
    return DocSpec(first, [second, *plain("Текст роботи.")])


def test_header_continued_on_next_paragraph(run_check):
    file_name = "Іваненко Іван – Юридичний супровід угод та підбір нерухомості, м Вишково, Закарпаття – ВІП67 – ДЗ №3.doc"
    result = run_check(_two_paragraph_header(), file_name)
    assert result.work_type == WorkType.DZ
    assert result.extracted_data.package == "ВІП" and result.extracted_data.flow_number == "67"
    assert not codes(result) & {"missing_package", "missing_work_type_marker", "wrong_body_text_size", "unclear_work_type"}


def test_file_name_cut_mid_header_is_not_an_error(run_check):
    cut = "Іваненко Іван – Юридичний супровід угод та підбір нерухомості, -.doc"
    assert codes(run_check(_two_paragraph_header(), cut)) == set()


def test_windows_short_file_name_is_not_an_error(run_check):
    # довгу назву замінено 8.3-ім'ям дорогою до сервісу — перевіряємо лише перший рядок
    for short in ("APPLE-~1.docx", "APPLE-~1", "IVANEN~2.DOC"):
        assert codes(run_check(DocSpec(header_parts()), short)) == set()


def test_file_name_without_package_is_still_an_error(run_check):
    result = run_check(DocSpec(header_parts()), "Іваненко Іван – Кав'ярня – Київ.doc")
    assert {"missing_package", "missing_work_type_marker"} <= codes(result)


def _sloppy_header() -> list[Part]:
    return [Part("Іваненко Іван_"), Part("Кафе", bold=True), Part("-Ужгород-"), Part("ВІП67", bold=True, color=RED), Part("-")]


def test_dz_marker_on_its_own_12pt_line_is_part_of_header(run_check):
    body = [[Part("ДЗ 6", size=12)], *plain("Текст роботи.")]
    result = run_check(DocSpec(_sloppy_header(), body), "Іваненко Іван – Кафе – Ужгород – ВІП67 – ДЗ №6.docx")
    assert result.work_type == WorkType.DZ
    assert result.extracted_data.student_name == "Іваненко Іван" and result.extracted_data.city == "Ужгород"
    assert "wrong_first_line_size" not in codes(result) and "unclear_work_type" not in codes(result)


def test_header_continued_with_city_and_package(run_check):
    # Enter після бізнесу: «Чернівці-ВІП67 - ДЗ №6» на другому рядку
    first = [Part("Іваненко Іван- "), Part("Товари для дому", bold=True), Part(" –")]
    second = [Part("Чернівці-"), Part("ВІП67", bold=True, color=RED), Part(" - ДЗ №6")]
    result = run_check(DocSpec(first, [second, *plain("Текст роботи.")]), "Іваненко Іван – Товари для дому – Чернівці – ВІП67 – ДЗ №6.docx")
    assert result.extracted_data.city == "Чернівці" and result.extracted_data.package == "ВІП"
    assert result.issues == []


def test_badly_signed_work_is_still_checked(run_check):
    # Тип роботи — з рядка «ДЗ 6» після порожнього рядка; назва файлу без типу
    body = [[Part("")], [Part("")], [Part("ДЗ 6")], *plain("Текст роботи.")]
    result = run_check(DocSpec(_sloppy_header(), body), "document.docx")
    assert result.work_type == WorkType.DZ
    assert "unclear_work_type" not in codes(result)


def test_staff_lines_are_not_student_text():
    from app.services.document_loader import load_docx

    doc = load_docx(build_docx(DocSpec(header_parts(), plain("Текст роботи.", "ГП 200$", "Куратор: Марія"))), 50 << 20)
    assert [p.text for p in doc.body_paragraphs] == ["Текст роботи."]


def test_dz2_point_a_without_currency_is_not_missing(run_check):
    # Точку А розписано формулою, але без $ — це «немає валюти», а не «немає точки А»
    body = plain("А = 70 × 30 000 = 2 100 000")
    result = run_check(DocSpec(header_parts(marker="ДЗ №2"), body), DZ2_FILE)
    assert "missing_currency" in codes(result) and "missing_point_a" not in codes(result)
    ok = run_check(DocSpec(header_parts(marker="ДЗ №2"), plain("Точка А: 120 клієнтів × 50 $ = 6 000 $")), DZ2_FILE)
    assert not codes(ok) & {"missing_currency", "missing_point_a"}


def test_issue_messages_have_no_abbreviated_formulas():
    from app.issues import ISSUE_CATALOG

    for code, spec in ISSUE_CATALOG.items():
        assert not re.search(r"\bт\.\s?[АБ]\b|Кл\s*×|\bнапр\.", spec.description), code
