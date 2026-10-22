"""Розбір воронок: растрові (OpenCV + OCR) та векторні (геометрія з PDF)."""

from app.visual.layout import Box, Token, analyze_stages, classify_token, find_chains, is_conversion_correct
from app.visual.pdf import PdfPage, _merge_percent_signs, find_small_text, find_vector_funnels
from app.visual.raster import analyze_image_funnel, decode_image, detect_blocks
from tests.builders import FUNNEL_BLOCKS, blank_image, funnel_image, funnel_tokens, tok

# --------------------------------------------------------------------- raster


def _img():
    return decode_image(funnel_image())


def test_detect_blocks_finds_each_stage():
    blocks = detect_blocks(_img())
    assert len(blocks) == 3
    for block, (top, bottom, _, _) in zip(blocks, FUNNEL_BLOCKS):
        assert abs(block.y1 - top) <= 5 and abs(block.y2 - bottom) <= 5


def test_correct_image_funnel():
    analysis = analyze_image_funnel(_img(), funnel_tokens())
    assert analysis.is_funnel
    assert [s.value for s in analysis.stages] == [10000, 2000, 500]
    assert [s.label for s in analysis.stages] == ["Охоплення", "Ліди", "Продажі"]
    assert [c.stated for c in analysis.conversions] == [20, 25]
    assert all(is_conversion_correct(c) for c in analysis.conversions)


def test_value_split_by_ocr_is_joined():
    tokens = [t for t in funnel_tokens() if t.text != "10 000"]
    cy = (FUNNEL_BLOCKS[0][0] + FUNNEL_BLOCKS[0][1]) / 2
    tokens += [Token("10", 620, cy - 20, 680, cy + 20, 0.9), Token("000", 690, cy - 20, 780, cy + 20, 0.9)]
    analysis = analyze_image_funnel(_img(), tokens)
    assert [s.value for s in analysis.stages] == [10000, 2000, 500]


def test_rounding_and_truncation_are_accepted():
    tokens = funnel_tokens(values=("9 000", "2 600", "700"), conversions=("28%", "27%"))  # 28,9% та 26,9%
    analysis = analyze_image_funnel(_img(), tokens)
    assert all(is_conversion_correct(c) for c in analysis.conversions)


def test_low_confidence_tokens_are_ignored():
    tokens = [Token(t.text, t.x1, t.y1, t.x2, t.y2, 0.1) for t in funnel_tokens()]
    analysis = analyze_image_funnel(_img(), tokens)
    assert analysis.unreadable  # форма воронки є, а прочитати нічого не вдалося -> ручна перевірка


def test_plain_image_is_not_a_funnel():
    analysis = analyze_image_funnel(decode_image(blank_image()), [])
    assert not analysis.is_funnel and not analysis.unreadable


# --------------------------------------------------------------------- vector


def _pyramid(levels: int = 4, top: float = 100, height: float = 30) -> list[Box]:
    return [Box(100 + 15 * i, top + height * i, 300 - 15 * i, top + height * (i + 1)) for i in range(levels)]


def _infos(tokens):
    return [classify_token(t) for t in tokens]


def test_find_chains_detects_pyramid_and_skips_org_chart():
    org = [Box(100, 400, 180, 430), Box(100, 450, 180, 480), Box(100, 500, 180, 530)]  # однакові блоки
    chains = find_chains(_pyramid() + org)
    assert len(chains) == 1 and len(chains[0]) == 4


def test_hypothetical_funnel_with_labels_right_is_fine():
    blocks = _pyramid(3)
    tokens = [tok(f"Крок {i}", 360, b.cy, w=60, h=12) for i, b in enumerate(blocks, 1)]
    analysis = analyze_stages(blocks, _infos(tokens), side_limit=90)
    assert analysis.is_hypothetical
    assert all(s.label for s in analysis.stages)
    assert not analysis.extra_labels and not analysis.stated_conversions


def test_labels_inside_and_extra_labels():
    blocks = _pyramid(3)
    inside = [tok("Охоплення", 200, b.cy, w=60, h=12) for b in blocks]
    extra = [tok("Зайвий крок", 360, blocks[-1].y2 + 30, w=60, h=12)]
    analysis = analyze_stages(blocks, _infos(inside + extra), side_limit=90)
    assert all(s.inside_text and not s.label for s in analysis.stages)
    assert analysis.extra_labels == ["Зайвий крок"]


def test_conversion_position_and_values():
    blocks = _pyramid(3)
    tokens = [tok(v, 200, b.cy, w=40, h=12) for v, b in zip(("1 000", "200", "50"), blocks)]
    tokens += [tok(lbl, 360, b.cy, w=60, h=12) for lbl, b in zip(("Охоплення", "Кліки", "Покупки"), blocks)]
    tokens.append(tok("20%", 70, blocks[0].y2, w=30, h=12))  # ліворуч між 1 і 2 — правильно
    tokens.append(tok("25%", 330, blocks[1].y2 + 5, w=30, h=12))  # праворуч — неправильне місце
    analysis = analyze_stages(blocks, _infos(tokens), side_limit=90)
    assert [s.value for s in analysis.stages] == [1000, 200, 50]
    assert analysis.conversions[0].stated == 20 and analysis.conversions[1].stated is None
    assert analysis.misplaced_conversions == ["25%"]


def test_body_text_crossing_funnel_is_not_a_label():
    blocks = _pyramid(3)
    tokens = [tok(f"Крок {i}", 360, b.cy, w=60, h=12) for i, b in enumerate(blocks, 1)]
    # Рядок основного тексту над воронкою, що перетинає її по ширині
    tokens += [Token(w, 60 + 50 * i, 80, 100 + 50 * i, 92) for i, w in enumerate("Опишіть вашу точку Б у вигляді воронки".split())]
    analysis = analyze_stages(blocks, _infos(tokens), side_limit=90)
    assert analysis.extra_labels == []


def _first_step(conversion: str):
    blocks = _pyramid(3)
    tokens = [tok(v, 200, b.cy, w=40, h=12) for v, b in zip(("90 456", "217 947", "2 527"), blocks)]
    tokens += [tok(lbl, 360, b.cy, w=60, h=12) for lbl, b in zip(("Охват", "Показ", "Клік"), blocks)]
    tokens += [tok(conversion, 70, blocks[0].y2, w=30, h=12), tok("1,15%", 70, blocks[1].y2, w=30, h=12)]
    return analyze_stages(blocks, _infos(tokens), side_limit=90).conversions[0]


def test_first_step_frequency_without_percent_is_not_checked():
    # Методичка, п. 10: частоту показів пишуть без % — за формулою не рахується
    check = _first_step("2,4")
    assert check.frequency and is_conversion_correct(check)


def test_first_step_with_percent_is_checked():
    # 217 947 / 90 456 × 100 = 240,9%
    assert is_conversion_correct(_first_step("240%"))
    assert is_conversion_correct(_first_step("2,4%"))  # та сама частота, записана з «%»
    wrong = _first_step("140%")
    assert not wrong.frequency and not is_conversion_correct(wrong)


def test_percent_sign_wrapped_to_next_line_belongs_to_number():
    # Вузьке текстове поле: «140» в одному рядку, «%» під ним
    words = [Token("140", 151, 416, 169, 428), Token("%", 151, 428, 161, 440), Token("Показ", 416, 433, 446, 445)]
    page = PdfPage(1, 595, 842, [], words)
    merged = _merge_percent_signs(page.words)
    assert [w.text for w in merged] == ["140%", "Показ"]


def test_vector_funnel_from_pdf_page():
    blocks = _pyramid(3)
    words = [tok(v, 200, b.cy, w=40, h=12) for v, b in zip(("1 000", "200", "50"), blocks)]
    words += [tok(lbl, 360, b.cy, w=60, h=12) for lbl, b in zip(("Охоплення", "Кліки", "Покупки"), blocks)]
    page = PdfPage(number=1, width=595, height=842, shapes=blocks, words=words)
    [(page_no, analysis)] = find_vector_funnels([page])
    assert page_no == 1 and [s.label for s in analysis.stages] == ["Охоплення", "Кліки", "Покупки"]


def test_small_text_in_drawings():
    body = [Token(f"слово{i}", 0, i * 14, 40, i * 14 + 12, 1.0, 12.0) for i in range(60)]
    tiny = [Token(f"підпис{i}", 0, i * 6, 30, i * 6 + 5, 1.0, 5.5) for i in range(20)]
    assert len(find_small_text([PdfPage(1, 595, 842, [], body + tiny)])) == 20
    assert find_small_text([PdfPage(1, 595, 842, [], body)]) == []


def test_hypothesis_funnel_must_have_values():
    from app.checkers.funnel import check_funnels
    from app.issues import IssueCollector
    from app.services.document_loader import ParsedDocument
    from tests.conftest import make_settings

    blocks = _pyramid(3)
    words = [tok(lbl, 360, b.cy, w=60, h=12) for lbl, b in zip(("Охоплення", "Кліки", "Покупки"), blocks)]
    pages = [PdfPage(number=1, width=595, height=842, shapes=blocks, words=words)]
    doc = ParsedDocument(paragraphs=[])

    for require_values, expected in ((True, {"missing_funnel_conversion"}), (False, set())):
        issues = IssueCollector()
        check_funnels(doc, None, pages, make_settings(), issues, require_values=require_values)
        assert {i.code for i in issues.issues} == expected


class FakeRegionReader:
    """Відповідає за фрагмент залежно від його положення: центр блоку / праворуч / ліворуч."""

    def __init__(self, values, labels, conversions):
        self.values, self.labels, self.conversions = list(values), list(labels), list(conversions)

    def read_region(self, image, single_line, digits=False):
        if single_line:
            return self.values.pop(0) if self.values else ""
        if digits:
            return self.conversions.pop(0) if self.conversions else ""
        return self.labels.pop(0) if self.labels else ""


def test_region_ocr_reads_values_labels_and_conversions(monkeypatch):
    from app.visual import raster
    from app.visual.raster import read_funnel_regions

    monkeypatch.setattr(raster, "REGION_OCR_WORKERS", 1)  # фейковий рушій віддає відповіді по черзі

    img = _img()
    blocks = detect_blocks(img)
    reader = FakeRegionReader(["10 000", "2 000", "500"], ["Охоплення", "Ліди", "Продажі"], ["20%", "25"])
    tokens = read_funnel_regions(img, blocks, reader)
    analysis = analyze_stages(blocks, [classify_token(t) for t in tokens])
    assert [s.value for s in analysis.stages] == [10000, 2000, 500]
    assert [s.label for s in analysis.stages] == ["Охоплення", "Ліди", "Продажі"]
    # «25» без знака відсотка ліворуч між блоками — це конверсія, OCR загубив «%»
    assert [c.stated for c in analysis.conversions] == [20, 25]


def test_step_numbering_top_down_is_wrong_order():
    from app.checkers.funnel import _report
    from app.issues import IssueCollector
    from tests.conftest import make_settings

    blocks = _pyramid(4)
    tokens = [tok(v, 200, b.cy, w=30, h=12) for v, b in zip(("4.", "3.", "2.", "1."), blocks)]
    tokens += [tok(f"Крок {i}", 360, b.cy, w=60, h=12) for i, b in enumerate(blocks)]
    analysis = analyze_stages(blocks, _infos(tokens), side_limit=90)
    issues = IssueCollector()
    _report("Воронка", analysis, make_settings(), issues, check_numbers=True, require_values=False)
    assert {i.code for i in issues.issues} == {"wrong_funnel_order"}


def _conversion(stated: str, upper: float, lower: float):
    blocks = _pyramid(2)
    tokens = [tok(f"{upper:g}", 200, blocks[0].cy, w=40, h=12), tok(f"{lower:g}", 200, blocks[1].cy, w=40, h=12)]
    tokens.append(tok(stated, 70, blocks[0].y2, w=30, h=12))
    return analyze_stages(blocks, _infos(tokens), side_limit=90).conversions[0]


def test_conversion_precision_follows_what_student_wrote():
    # Воронка з реальної роботи: «Показ» 217 947 → «Клік» 2 527 = 1,159%; «Клік» 2 527 → «Подписки» 193 = 7,637%
    for stated in ("1%", "1,2%", "1,1%", "1,15%", "1,16%"):
        assert is_conversion_correct(_conversion(stated, 217947, 2527)), stated
    for stated in ("1,9%", "1,3%", "1,17%"):
        assert not is_conversion_correct(_conversion(stated, 217947, 2527)), stated
    assert is_conversion_correct(_conversion("7,6%", 2527, 193))
    assert not is_conversion_correct(_conversion("8,5%", 2527, 193))
    # Округлення вгору до цілого трапляється у прийнятих роботах (71,43% → 72%), а на 1 п.п. і більше — ні
    assert is_conversion_correct(_conversion("72%", 70, 50))
    assert not is_conversion_correct(_conversion("73%", 70, 50))
