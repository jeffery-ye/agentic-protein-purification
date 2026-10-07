"""
Tests for MethodsTool.parse_article.

Sections are selected by title: a matching title at any depth, in the body
or in <back>, with `sec-type` as a backup. These tests pin the structures a
top-level-only match missed, using hand-made snippets.
"""

import pytest

from agent_engine.agent_tools.methods_tool import MethodsTool


@pytest.fixture
def tool():
    return MethodsTool()


def test_extracts_identifiers_and_title(tool, pmc_article):
    parsed = tool.parse_article(pmc_article)

    assert parsed["pmcid"] == "PMC1234567"
    # Inline markup is flattened and whitespace collapsed.
    assert parsed["title"] == "Crystal structure of a Mycobacterium tuberculosis kinase"


def test_missing_pmcid_and_title_fall_back(tool):
    parsed = tool.parse_article("<article><body><p>text</p></body></article>")

    assert parsed["pmcid"] == "Not Found"
    assert parsed["title"] is None


def test_methods_section_matched_by_heading_keyword(tool, pmc_article):
    methods = tool.parse_article(pmc_article)["methods"]

    assert "50 mM Tris pH 8.0" in methods
    # Introduction and Results headings do not match, so their prose stays out.
    assert "Kinases are important" not in methods
    assert "structure was solved" not in methods


def test_nested_subsection_paragraphs_are_included(tool, pmc_article):
    """A subsection under a matching heading is folded into that section."""
    methods = tool.parse_article(pmc_article)["methods"]

    assert "Ni-NTA resin and eluted with 250 mM imidazole" in methods


@pytest.mark.parametrize(
    "heading",
    [
        "Materials and Methods",
        "METHODS",
        "Experimental Procedures",
        "Protein Expression",
        "Purification",
    ],
)
def test_heading_matching_is_case_insensitive_substring(tool, heading):
    xml = (
        f"<article><body><sec><title>{heading}</title>"
        "<p>Buffer contained 20 mM HEPES.</p></sec></body></article>"
    )

    assert "20 mM HEPES" in tool.parse_article(xml)["methods"]


def test_returns_none_when_no_heading_matches(tool):
    xml = (
        "<article><body><sec><title>Discussion</title>"
        "<p>We discuss the result.</p></sec></body></article>"
    )

    assert tool.parse_article(xml)["methods"] is None


def test_body_without_sections_yields_no_methods(tool):
    """No section title to match, and no whole-article fallback."""
    xml = "<article><body><p>Protein was purified on Ni-NTA.</p></body></article>"

    parsed = tool.parse_article(xml)
    assert parsed["methods"] is None
    assert "full_text" not in parsed


# --- Structures #28 found the old heading match missed (#29) -----------------
# Hand-made snippets, one per structure. No text from real articles.


def test_purification_subsection_under_results_is_found(tool, pmc_article):
    """A matching title counts at any depth, not only at the top level."""
    methods = tool.parse_article(pmc_article)["methods"]

    assert "SeMet protein was purified" in methods
    # Its non-matching parent's own prose stays out.
    assert "structure was solved" not in methods


def test_methods_in_back_matter_are_found(tool):
    """Nature-style articles put their methods in <back>."""
    xml = (
        "<article><body><sec><title>Results</title><p>We solved it.</p></sec></body>"
        "<back><sec><title>Methods</title><sec><title>Protein production</title>"
        "<p>Eluted with 300 mM imidazole.</p></sec></sec></back></article>"
    )

    assert tool.parse_article(xml)["methods"] == "Eluted with 300 mM imidazole."


def test_sec_type_marks_a_methods_section_with_an_unusual_title(tool):
    xml = (
        '<article><body><sec sec-type="materials|methods"><title>Online content</title>'
        "<p>Dialysed against 20 mM HEPES.</p></sec></body></article>"
    )

    assert tool.parse_article(xml)["methods"] == "Dialysed against 20 mM HEPES."


def test_a_nested_match_is_not_repeated(tool):
    """A purification subsection inside a methods section is taken once, with its parent."""
    xml = (
        "<article><body><sec><title>Methods</title><p>Cloning.</p>"
        "<sec><title>Purification</title><p>Ni-NTA.</p></sec></sec></body></article>"
    )

    assert tool.parse_article(xml)["methods"] == "Cloning.\nNi-NTA."


def test_untitled_sections_yield_no_methods(tool):
    """No title to match, and no whole-article fallback."""
    xml = (
        "<article><body><sec><title>Introduction</title><p>Background.</p></sec>"
        "<sec><p>Cells were lysed and purified on Ni-NTA.</p></sec></body></article>"
    )

    assert tool.parse_article(xml)["methods"] is None


def test_a_table_in_a_methods_section_is_read_once(tool):
    xml = (
        "<article><body><sec><title>Methods</title><p>See Table 1.</p>"
        "<table-wrap><label>Table 1</label><caption><p>Buffers used.</p></caption>"
        "<table><tr><th>Step</th><th>Buffer</th></tr>"
        "<tr><td>Wash</td><td>20 mM Tris, 500 mM NaCl</td></tr></table>"
        "</table-wrap></sec></body></article>"
    )

    methods = tool.parse_article(xml)["methods"]
    assert (
        methods
        == "See Table 1.\nTable 1 Buffers used.\nStep\tBuffer\nWash\t20 mM Tris, 500 mM NaCl"
    )


def test_a_table_outside_the_methods_is_not_read(tool):
    xml = (
        "<article><body><sec><title>Methods</title><p>Ni-NTA.</p></sec>"
        "<sec><title>Results</title><table-wrap><table>"
        "<tr><td>Crystal</td><td>10 mM HEPES</td></tr></table></table-wrap></sec>"
        "</body></article>"
    )

    assert tool.parse_article(xml)["methods"] == "Ni-NTA."


def test_every_abstract_paragraph_is_kept(tool, pmc_article):
    assert tool.parse_article(pmc_article)["abstract"] == (
        "First abstract paragraph. Second abstract paragraph."
    )


def test_a_structured_abstract_is_kept_whole(tool):
    xml = (
        "<article><front><article-meta><abstract>"
        "<sec><title>Background</title><p>Why.</p></sec>"
        "<sec><title>Results</title><p>What.</p></sec>"
        "</abstract></article-meta></front><body/></article>"
    )

    assert tool.parse_article(xml)["abstract"] == "Why. What."


def test_a_list_inside_a_paragraph_is_read_once(tool):
    """JATS nests lists in a paragraph, whose own text already holds them."""
    xml = (
        "<article><body><sec><title>Methods</title><p>Buffers: "
        "<list><list-item><p>A: 50 mM Tris.</p></list-item></list></p>"
        "</sec></body></article>"
    )

    assert tool.parse_article(xml)["methods"].count("50 mM Tris") == 1


def test_a_table_inside_a_paragraph_is_read_once(tool):
    xml = (
        "<article><body><sec><title>Methods</title><p>See the table."
        "<table-wrap><table><tr><td>Lysis</td><td>50 mM Tris</td></tr></table></table-wrap>"
        "</p></sec></body></article>"
    )

    assert tool.parse_article(xml)["methods"].count("50 mM Tris") == 1
