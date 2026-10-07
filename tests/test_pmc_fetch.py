"""
Fetching a PMC article: every failure is a retrieval failure, counted apart from
access restrictions (#20), and a reply that isn't a readable article is never
taken for an open one. The fetch itself is bounded in time and tried once per
attempt (#95).
"""

import urllib.error

import pytest
import requests
from Bio import Entrez

from agent_engine import settings
from agent_engine.agent_tools import grounding_tool
from agent_engine.agent_tools.grounding_tool import GroundingTool
from agent_engine.models import HitStatus


@pytest.fixture
def replies(monkeypatch):
    """The PMC fetch answering from a list, one reply or exception per call."""
    queue = []

    def fetch_pmc_xml(pmc_id):
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply.decode("utf-8")

    monkeypatch.setattr(grounding_tool, "fetch_pmc_xml", fetch_pmc_xml)
    monkeypatch.setattr(grounding_tool.time, "sleep", lambda _s: None)
    return queue


def _http(code):
    return urllib.error.HTTPError("url", code, "error", None, None)


ARTICLE = b"<pmc-articleset><article><front/><body><p>Ni-NTA.</p></body></article></pmc-articleset>"


@pytest.mark.parametrize("code", [400, 429, 500, 502, 503, 504])
def test_transient_http_errors_are_retried(replies, code):
    replies.extend([_http(code), ARTICLE])

    article, status = GroundingTool().search_pmc("PMC1")

    assert status is None and "Ni-NTA" in article


def test_other_http_errors_are_a_retrieval_failure_without_retries(replies):
    replies.extend([_http(404), ARTICLE])

    assert GroundingTool().search_pmc("PMC1") == (None, HitStatus.PMC_RETRIEVAL_FAILED)
    assert len(replies) == 1  # not retried


def test_every_retry_failing_is_a_retrieval_failure(replies):
    replies.extend([_http(429)] * 3)

    assert GroundingTool().search_pmc("PMC1") == (None, HitStatus.PMC_RETRIEVAL_FAILED)


@pytest.mark.parametrize(
    "reply",
    [
        b"<pmc-articleset><error>The following PMCID is not available: PMC1</error></pmc-articleset>",
        b"not xml at all",
    ],
)
def test_a_reply_with_no_article_is_a_retrieval_failure(replies, reply):
    replies.append(reply)

    assert GroundingTool().search_pmc("PMC1") == (None, HitStatus.PMC_RETRIEVAL_FAILED)


def test_an_article_with_no_body_is_withheld_whatever_the_notice_says(replies):
    """A reworded withholding notice still reads as closed, not as an empty open article."""
    replies.append(
        b"<pmc-articleset><article><front><article-meta><title-group>"
        b"<article-title>A paper</article-title></title-group></article-meta></front>"
        b"<!-- The publisher has not released this full text. --></article></pmc-articleset>"
    )

    assert GroundingTool().search_pmc("PMC1") == (None, HitStatus.NO_OPEN_ACCESS)


def test_the_withholding_notice_is_still_recognised(replies):
    replies.append(b"<error>The publisher does not allow downloading</error>")

    assert GroundingTool().search_pmc("PMC1") == (None, HitStatus.NO_OPEN_ACCESS)


def test_the_contact_address_is_checked_when_a_run_starts_not_at_import(monkeypatch):
    monkeypatch.delenv("ENTREZ_EMAIL", raising=False)

    with pytest.raises(ValueError, match="ENTREZ_EMAIL"):
        GroundingTool()
    with pytest.raises(ValueError, match="ENTREZ_EMAIL"):
        settings.entrez_email()


# --- The fetch itself (#95) --------------------------------------------------


class _Reply:
    def __init__(self, content: bytes, status=200, content_type="text/xml; charset=UTF-8"):
        self.content = content
        self.status_code = status
        self.reason = "reason"
        self.url = grounding_tool.EFETCH_URL
        self.headers = requests.structures.CaseInsensitiveDict(
            {"Content-Type": content_type} if content_type is not None else {}
        )


@pytest.fixture
def http(monkeypatch):
    """requests.get answering from a list, recording each call's arguments."""
    queue, calls = [], []

    def get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(grounding_tool.requests, "get", get)
    monkeypatch.setattr(grounding_tool.time, "sleep", lambda _s: None)
    return queue, calls


def test_a_hanging_fetch_times_out_and_is_a_retrieval_failure(http):
    queue, calls = http
    queue.extend([requests.ReadTimeout("read timed out")] * 3)

    assert GroundingTool().search_pmc("PMC1") == (None, HitStatus.PMC_RETRIEVAL_FAILED)
    assert [call["timeout"] for call in calls] == [grounding_tool.EFETCH_TIMEOUT] * 3


def test_the_fetch_requests_the_url_entrez_efetch_did(http, monkeypatch):
    """Same endpoint, parameters and identification as Biopython's own request."""
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    queue, calls = http
    queue.append(_Reply(b"<article/>"))
    GroundingTool()  # sets Entrez.email

    grounding_tool.fetch_pmc_xml("PMC1")

    [call] = calls
    ours = requests.Request("GET", call["url"], params=call["params"]).prepare().url
    theirs = Entrez._build_request(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        {"db": "pmc", "id": "PMC1", "retmode": "xml"},
    ).full_url
    assert ours == theirs


def test_the_api_key_is_sent_when_set(http, monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "key123")
    queue, calls = http
    queue.append(_Reply(b"<article/>"))

    grounding_tool.fetch_pmc_xml("PMC1")

    assert calls[0]["params"]["api_key"] == "key123"


@pytest.mark.parametrize(
    "content_type, expected",
    [
        # Biopython returned these as bytes, which were decoded unchanged.
        ("text/xml; charset=UTF-8", "<a>\r\nµ</a>"),
        # And these through a TextIOWrapper, which turns \r\n into \n.
        ("text/plain", "<a>\nµ</a>"),
        (None, "<a>\nµ</a>"),
    ],
)
def test_the_fetch_returns_the_text_entrez_efetch_did(http, content_type, expected):
    queue, _ = http
    queue.append(_Reply("<a>\r\nµ</a>".encode("utf-8"), content_type=content_type))

    assert grounding_tool.fetch_pmc_xml("PMC1") == expected


def test_an_error_status_raises_the_http_error_entrez_efetch_did(http):
    queue, _ = http
    queue.append(_Reply(b"busy", status=503))

    with pytest.raises(urllib.error.HTTPError) as raised:
        grounding_tool.fetch_pmc_xml("PMC1")
    assert raised.value.code == 503
