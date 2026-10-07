"""
Parse a PMC JATS article into its title, abstract and methods text.

Methods are found by section title (#29, from #28's findings): a section whose
title names methods, protocols, purification or expression is taken with its
subsections, at any depth, in the body or in <back>, where Nature-style
articles put their methods. `sec-type="methods"` marks a methods section too,
as a backup: fewer than half of open articles set it. Tables inside a methods
section are read with it, since buffer compositions are often tabulated.

An article with no methods section yields no methods text; the pipeline does
not fall back to the whole article.
"""

import re
import xml.etree.ElementTree as ET

METHODS_KEYWORDS = ("method", "experimental", "protocol", "purification", "expression")


def _text(element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def _title(sec) -> str:
    title = sec.find("./title")
    return _text(title) if title is not None else ""


def _is_methods(sec) -> bool:
    title = _title(sec).lower()
    if any(keyword in title for keyword in METHODS_KEYWORDS):
        return True
    sec_type = (sec.get("sec-type") or "").lower()
    return "method" in sec_type


def _table_text(table_wrap) -> str:
    """A table's label and caption, then one tab-separated line per row."""
    lines = []
    head = " ".join(
        _text(part)
        for part in (table_wrap.find("./label"), table_wrap.find("./caption"))
        if part is not None
    )
    if head:
        lines.append(head)
    for row in table_wrap.iter("tr"):
        cells = [_text(cell) for cell in row if cell.tag in ("td", "th")]
        if any(cells):
            lines.append("\t".join(cells))
    return "\n".join(lines)


def _inside_paragraphs(root) -> set[int]:
    """
    The ids of elements inside a <p>. JATS nests lists, and even tables, in a
    paragraph, and the paragraph's own text already holds theirs.
    """
    return {id(e) for p in root.iter("p") for e in p.iter() if e is not p}


def _section_text(sec, nested: set[int]) -> list[str]:
    """
    Every paragraph and table in a section and its subsections, in document
    order, once. A table's own paragraphs (caption, footnotes) come with the
    table, and anything inside a paragraph comes with the paragraph.
    """
    in_tables = {id(p) for t in sec.iter("table-wrap") for p in t.iter("p")}
    segments = []
    for element in sec.iter():
        if id(element) in nested:
            continue
        if element.tag == "p" and id(element) not in in_tables:
            text = _text(element)
        elif element.tag == "table-wrap":
            text = _table_text(element)
        else:
            continue
        if text:
            segments.append(text)
    return segments


class MethodsTool:
    def parse_article(self, xml_article):
        root = ET.fromstring(xml_article)

        pmcid_element = root.find(".//article-id[@pub-id-type='pmcid']")
        pmcid = pmcid_element.text if pmcid_element is not None else "Not Found"

        title_element = root.find(".//article-title")
        article_title = (_text(title_element) if title_element is not None else "") or None

        # Every paragraph, structured abstracts included.
        abstract = root.find(".//abstract")
        abstract_paragraphs = [_text(p) for p in abstract.iter("p")] if abstract is not None else []
        abstract_text = " ".join(p for p in abstract_paragraphs if p) or "N/A"

        containers = [c for c in (root.find(".//body"), root.find(".//back")) if c is not None]

        nested = _inside_paragraphs(root)

        # Methods: the outermost matching sections, each with all its subsections.
        methods_sections = []

        def collect(sec):
            if _is_methods(sec):
                segments = _section_text(sec, nested)
                if segments:
                    methods_sections.append("\n".join(segments))
                return
            for child in sec.findall("./sec"):
                collect(child)

        for container in containers:
            for sec in container.findall("./sec"):
                collect(sec)

        methods = "\n\n".join(methods_sections) if methods_sections else None

        return {
            "pmcid": pmcid,
            "title": article_title,
            "abstract": abstract_text,
            "methods": methods,
        }
