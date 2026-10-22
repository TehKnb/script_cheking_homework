"""Обчислення ефективного форматування run-ів з урахуванням успадкування стилів.

python-docx повертає лише явно задані властивості run-а (`run.font.name` часто None),
тому значення шукається по ланцюжку, як це робить Word:
run -> стиль символів (rStyle + basedOn) -> стиль абзацу (pStyle + basedOn) -> docDefaults.
Теми шрифтів (`asciiTheme="minorHAnsi"`) розв'язуються через theme1.xml.
"""

from __future__ import annotations

from lxml import etree

from docx.document import Document as DocxDocument
from docx.oxml.ns import qn

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_TRUE_VALUES = {None, "1", "true", "on"}

# Якщо шрифт не задано ніде, Word використовує Times New Roman, а розмір 10 pt.
WORD_FALLBACK_FONT = "Times New Roman"
WORD_FALLBACK_SIZE_PT = 10.0


class StyleResolver:
    def __init__(self, document: DocxDocument) -> None:
        styles_el = document.styles.element
        self._styles: dict[str, etree._Element] = {}
        self._default_paragraph_style: str | None = None
        for style in styles_el.findall(qn("w:style")):
            style_id = style.get(qn("w:styleId"))
            if not style_id:
                continue
            self._styles[style_id] = style
            if style.get(qn("w:type")) == "paragraph" and style.get(qn("w:default")) in ("1", "true"):
                self._default_paragraph_style = style_id

        self._doc_defaults_rpr = styles_el.find(f"{qn('w:docDefaults')}/{qn('w:rPrDefault')}/{qn('w:rPr')}")
        self._theme_fonts = self._load_theme_fonts(document)
        self._chain_cache: dict[str | None, list[etree._Element]] = {}

    # ------------------------------------------------------------------ public

    def rpr_chain(self, run_el: etree._Element, paragraph_el: etree._Element) -> list[etree._Element]:
        chain: list[etree._Element] = []
        rpr = run_el.find(qn("w:rPr"))
        if rpr is not None:
            chain.append(rpr)
            rstyle = rpr.find(qn("w:rStyle"))
            if rstyle is not None:
                chain.extend(self._style_rprs(rstyle.get(qn("w:val"))))

        pstyle_id = self._default_paragraph_style
        ppr = paragraph_el.find(qn("w:pPr"))
        if ppr is not None:
            pstyle = ppr.find(qn("w:pStyle"))
            if pstyle is not None and pstyle.get(qn("w:val")):
                pstyle_id = pstyle.get(qn("w:val"))
        chain.extend(self._style_rprs(pstyle_id))

        if self._doc_defaults_rpr is not None:
            chain.append(self._doc_defaults_rpr)
        return chain

    def font(self, chain: list[etree._Element], slot: str) -> str:
        """slot: 'ascii' (латиниця) або 'hAnsi' (кирилиця та інші символи)."""
        for rpr in chain:
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                continue
            theme = rfonts.get(qn(f"w:{slot}Theme"))
            if theme:
                resolved = self._theme_font(theme)
                if resolved:
                    return resolved
            name = rfonts.get(qn(f"w:{slot}"))
            if name:
                return name
        return WORD_FALLBACK_FONT

    @staticmethod
    def size_pt(chain: list[etree._Element]) -> float:
        for rpr in chain:
            sz = rpr.find(qn("w:sz"))
            if sz is not None and sz.get(qn("w:val")):
                try:
                    return int(sz.get(qn("w:val"))) / 2
                except ValueError:
                    continue
        return WORD_FALLBACK_SIZE_PT

    @staticmethod
    def bold(chain: list[etree._Element]) -> bool:
        return StyleResolver._toggle(chain, "w:b")

    @staticmethod
    def hidden(chain: list[etree._Element]) -> bool:
        return StyleResolver._toggle(chain, "w:vanish")

    @staticmethod
    def color(chain: list[etree._Element]) -> str | None:
        """HEX кольору (напр. '0000FF') або None для 'auto'/не задано."""
        for rpr in chain:
            color = rpr.find(qn("w:color"))
            if color is not None:
                val = (color.get(qn("w:val")) or "").strip()
                if not val or val.lower() == "auto":
                    return None
                return val.upper()
        return None

    # ----------------------------------------------------------------- private

    @staticmethod
    def _toggle(chain: list[etree._Element], tag: str) -> bool:
        for rpr in chain:
            el = rpr.find(qn(tag))
            if el is not None:
                return el.get(qn("w:val")) in _TRUE_VALUES
        return False

    def _style_rprs(self, style_id: str | None) -> list[etree._Element]:
        if style_id in self._chain_cache:
            return self._chain_cache[style_id]
        result: list[etree._Element] = []
        seen: set[str] = set()
        current = style_id
        while current and current not in seen and current in self._styles:
            seen.add(current)
            style = self._styles[current]
            rpr = style.find(qn("w:rPr"))
            if rpr is not None:
                result.append(rpr)
            based_on = style.find(qn("w:basedOn"))
            current = based_on.get(qn("w:val")) if based_on is not None else None
        self._chain_cache[style_id] = result
        return result

    def _theme_font(self, theme_ref: str) -> str | None:
        if theme_ref.startswith("minor"):
            return self._theme_fonts.get("minor")
        if theme_ref.startswith("major"):
            return self._theme_fonts.get("major")
        return None

    @staticmethod
    def _load_theme_fonts(document: DocxDocument) -> dict[str, str]:
        fonts: dict[str, str] = {}
        try:
            for part in document.part.package.iter_parts():
                if not str(part.partname).startswith("/word/theme/"):
                    continue
                root = etree.fromstring(part.blob, parser=etree.XMLParser(resolve_entities=False))
                for kind in ("minor", "major"):
                    latin = root.find(f".//{{{_A_NS}}}{kind}Font/{{{_A_NS}}}latin")
                    if latin is not None and latin.get("typeface"):
                        fonts[kind] = latin.get("typeface")
                break
        except Exception:  # тема необов'язкова: без неї просто не розв'язуємо theme-шрифти
            pass
        return fonts
