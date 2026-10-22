"""Правила, знайдені на відхилених ДЗ, які сервіс раніше пропускав: дрібний текст на схемах, опис під вершиною
воронки, описи у вузлах SmartArt-піраміди, конверсія біля порожньої воронки, матеріали «на Google Диску»."""

import io
import zipfile

from app.checkers.funnel import check_funnels
from app.issues import IssueCollector
from app.services.document_loader import ImageInfo, ParsedDocument, load_docx
from app.visual.layout import Box, Token, analyze_stages, classify_token
from tests.builders import DocSpec, build_docx, funnel_image, header_parts, plain, tok
from tests.conftest import codes, make_settings


class FakeOcr:
    """OCR без жодного прочитаного фрагмента; read() повертає задані слова (для виміру розміру тексту)."""

    name = "fake"

    def __init__(self, words: list[Token] | None = None) -> None:
        self._words = words or []

    def read(self, image):
        return self._words

    def read_region(self, image, single_line, digits=False):
        return ""


def _funnel_doc(**kwargs) -> ParsedDocument:
    # funnel_image: 1400×900 px, блоки воронки залиті суцільно, без написів
    image = ImageInfo("word/media/image1.png", funnel_image(), display_width_pt=311, display_height_pt=200)
    return ParsedDocument(paragraphs=[], images=[image], **kwargs)


def _check(doc: ParsedDocument, ocr=None) -> list[str]:
    issues = IssueCollector()
    check_funnels(doc, ocr or FakeOcr(), [], make_settings(), issues)
    return [i.code for i in issues.issues]


# ------------------------------------------------------------- дрібний текст


def _words(height_px: float) -> list[Token]:
    return [Token(f"Слово{i}", 100 * i, 600, 100 * i + 80, 600 + height_px, 0.9) for i in range(6)]


def test_small_text_on_diagram_image():
    # 12 px на зображенні висотою 900 px, показаному на 200 pt, — це ≈2,7 pt у документі
    assert "small_text" in _check(_funnel_doc(), FakeOcr(_words(12)))
    # 45 px -> 10 pt — нормально
    assert "small_text" not in _check(_funnel_doc(), FakeOcr(_words(45)))


# ------------------------------------------------------- порожня воронка-картинка


def test_blank_funnel_with_conversions_in_text_boxes():
    assert "funnel_conversion_without_values" in _check(_funnel_doc(shape_count=2, textbox_text="20%\n25%"))
    # Порожня (гіпотетична) воронка без конверсії — допустима в ДЗ
    assert "funnel_conversion_without_values" not in _check(_funnel_doc(shape_count=2, textbox_text="Охоплення"))


# --------------------------------------------------------- SmartArt-піраміда


def test_smartart_pyramid_with_descriptions_inside():
    labels = [["Створення відеороликів", "Тестуємо креативи", "Людина написала", "Залишила номер"]]
    doc = ParsedDocument(paragraphs=[], has_smartart=True, smartart_pyramids=labels)
    assert "funnel_labels_inside" in _check(doc)
    numbers = ParsedDocument(paragraphs=[], has_smartart=True, smartart_pyramids=[["180121", "41354", "40", "20"]])
    assert "funnel_labels_inside" not in _check(numbers)


_DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _docx_with(body_xml: str, extra: dict[str, str]) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(build_docx(DocSpec(header_parts()))))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/document.xml":
                text = data.decode("utf-8")
                start = text.index("<w:body>") + len("<w:body>")
                data = (text[:start] + body_xml + text[start:]).encode("utf-8")
            target.writestr(item, data)
        for name, content in extra.items():
            target.writestr(name, content)
    return out.getvalue()


def _pt(text: str, kind: str | None = None, layout: str | None = None) -> str:
    attrs = f' type="{kind}"' if kind else ""
    props = f'<dgm:prSet loTypeId="urn:microsoft.com/office/officeart/2005/8/layout/{layout}"/>' if layout else ""
    body = f"<dgm:t><a:p><a:r><a:t>{text}</a:t></a:r></a:p></dgm:t>" if text else ""
    return f'<dgm:pt modelId="{{{text or kind}}}"{attrs}>{props}{body}</dgm:pt>'


def test_loader_reads_pyramid_nodes_and_counts_text_box_once():
    data_xml = (
        f'<dgm:dataModel xmlns:dgm="{_DGM}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"<dgm:ptLst>{_pt('', 'doc', 'pyramid3')}{_pt('Тестуємо креативи')}{_pt('Залишила номер')}"
        f"{_pt('', 'parTrans')}</dgm:ptLst></dgm:dataModel>"
    )
    textbox = "<w:txbxContent><w:p><w:r><w:t>20%</w:t></w:r></w:p></w:txbxContent>"
    body = (
        f'<w:p><w:r><w:drawing><dgm:relIds xmlns:dgm="{_DGM}"/></w:drawing></w:r></w:p>'
        f'<w:p><w:r><mc:AlternateContent xmlns:mc="{_MC}"><mc:Choice Requires="wps"><w:drawing>{textbox}</w:drawing>'
        f"</mc:Choice><mc:Fallback><w:pict>{textbox}</w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    doc = load_docx(_docx_with(body, {"word/diagrams/data1.xml": data_xml}), 50 << 20)
    assert doc.smartart_pyramids == [["Тестуємо креативи", "Залишила номер"]]
    assert doc.textbox_text == "20%"


# ------------------------------------------------------------ опис під вершиною


def test_label_below_funnel_tip_has_no_block():
    blocks = [Box(100 + 15 * i, 100 + 30 * i, 300 - 15 * i, 130 + 30 * i) for i in range(3)]
    tokens = [tok(f"Крок {i}", 360, b.cy, w=60, h=12) for i, b in enumerate(blocks, 1)]
    tokens.append(tok("Продаж", 250, blocks[-1].y2 + 10, w=40, h=12))  # під вершиною, правіше центру
    analysis = analyze_stages(blocks, [classify_token(t) for t in tokens], side_limit=90)
    assert analysis.extra_labels == ["Продаж"]
    assert [s.label for s in analysis.stages] == ["Крок 1", "Крок 2", "Крок 3"]


# ------------------------------------------------- матеріали «на Google Диску»


def test_materials_mentioned_on_drive_without_link(run_check):
    mention = DocSpec(header_parts(), plain("Креатив скинув через google диск."))
    assert "missing_material_link" in codes(run_check(mention))
    with_link = DocSpec(header_parts(), plain("Креатив на Google Диску."), hyperlink="https://drive.google.com/x")
    assert "missing_material_link" not in codes(run_check(with_link))


# ----------------------------------------------- розташування описів і конверсій


def _vector_funnel_codes(label_x: list[float], conv: list[tuple[float, float]]) -> list[str]:
    """Піраміда з 4 блоків (висота 30 pt); label_x — де починається опис кожного блоку,
    conv — (x правого краю, y центру) кожної конверсії."""
    from app.visual.pdf import PdfPage

    blocks = [Box(100 + 15 * i, 100 + 30 * i, 300 - 15 * i, 130 + 30 * i) for i in range(4)]
    words = [Token(v, b.cx - 15, b.cy - 6, b.cx + 15, b.cy + 6) for v, b in zip(("1000", "500", "100", "50"), blocks)]
    words += [Token(f"Крок{i}", x, b.cy - 6, x + 40, b.cy + 6) for i, (x, b) in enumerate(zip(label_x, blocks), 1)]
    words += [Token(t, x - 30, y - 6, x, y + 6) for t, (x, y) in zip(("50%", "20%", "50%"), conv)]
    doc = ParsedDocument(paragraphs=[], shape_count=4)
    issues = IssueCollector()
    check_funnels(doc, None, [PdfPage(1, 595, 842, blocks, words)], make_settings(), issues)
    return [i.code for i in issues.issues]


def _boundaries() -> list[float]:
    return [130.0, 160.0, 190.0]


def test_aligned_vector_funnel_passes():
    labels = [310, 295, 280, 265]  # рівно 10 pt від кожного блоку
    conv = [(100 + 15 * (i - 1) - 8, y) for i, y in enumerate(_boundaries(), 1)]
    assert _vector_funnel_codes(labels, conv) == []


def test_labels_scattered_or_far_from_blocks():
    conv = [(100 + 15 * (i - 1) - 8, y) for i, y in enumerate(_boundaries(), 1)]
    assert "funnel_labels_misaligned" in _vector_funnel_codes([385, 295, 280, 265], conv)  # перший — за 85 pt
    assert "funnel_labels_misaligned" in _vector_funnel_codes([350, 295, 320, 265], conv)  # врозкид: 50 / 10 / 40 / 10


def test_conversion_not_between_blocks():
    labels = [310, 295, 280, 265]
    conv = [(100 + 15 * (i - 1) - 8, y) for i, y in enumerate(_boundaries(), 1)]
    conv[0] = (92, 116)  # навпроти першого блоку, а не на межі 1–2
    assert "wrong_conversion_position" in _vector_funnel_codes(labels, conv)


def test_labels_in_straight_column_are_fine_but_outlier_is_not():
    conv = [(100 + 15 * (i - 1) - 8, y) for i, y in enumerate(_boundaries(), 1)]
    # Колонка на x=330 біля піраміди, що звужується: відстань до блоків 30 → 75 pt, але стоять рівно
    assert "funnel_labels_misaligned" not in _vector_funnel_codes([330, 330, 330, 330], conv)
    assert "funnel_labels_misaligned" in _vector_funnel_codes([330, 330, 355, 330], conv)


# ------------------------------------------------- поля налазять на воронку


def test_text_field_overlapping_funnel_block():
    from app.visual.pdf import PdfPage, find_vector_funnels

    # Трапеції, як у Word: скошені краї (ширина звужується на 26 pt за блок)
    blocks = [Box(100 + 13 * i, 100 + 30 * i, 300 - 13 * i, 130 + 30 * i) for i in range(3)]
    polygons = {
        b: [(b.x1, b.y1), (b.x2, b.y1), (b.x2 - 13, b.y2), (b.x1 + 13, b.y2)] for b in blocks
    }
    words = [Token(v, b.cx - 15, b.cy - 6, b.cx + 15, b.cy + 6) for v, b in zip(("1000", "500", "100"), blocks)]
    words += [Token(f"Крок{i}", b.x2 + 10, b.cy - 6, b.x2 + 50, b.cy + 6) for i, b in enumerate(blocks, 1)]
    words += [Token("50%", 70, 124, 95, 136), Token("20%", 85, 154, 108, 166)]

    def overlaps(frames: list[Box]) -> list[str]:
        page = PdfPage(1, 595, 842, blocks, words, frames, polygons)
        return find_vector_funnels([page])[0][1].overlapping_fields

    # Рамка «20%» біля скосу: за прямокутником блоку перетинається, а з самою трапецією — ні
    assert overlaps([Box(65, 120, 98, 140), Box(80, 150, 118, 170)]) == []
    # Рамка «20%» розтягнута на блок і закриває його
    assert overlaps([Box(65, 120, 98, 140), Box(80, 150, 140, 170)]) == ["20%"]
