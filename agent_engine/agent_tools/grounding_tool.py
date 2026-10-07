import html
import io
import re
import threading
import time
import urllib.error
import xml.etree.ElementTree as ET

import requests
from Bio import Entrez
from dotenv import load_dotenv

from ..models import HitStatus, Paper, PaperAccess
from ..settings import entrez_email, ncbi_api_key

load_dotenv()

RCSB_PUBMED_URL = "https://data.rcsb.org/rest/v1/core/pubmed/"
RCSB_ENTRY_URL = "https://data.rcsb.org/rest/v1/core/entry/"


def _plain_text(markup: str | None) -> str | None:
    """RCSB's abstracts carry inline HTML (<i>, <sub>); the report shows plain text."""
    if not markup:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return re.sub(r"\s+", " ", text).strip() or None


# HTTP errors from efetch worth another attempt: 400 is NCBI's answer to a
# burst, 429 its rate limit, and the 5xx are the service or a proxy. Any other
# code won't change on a retry.
RETRIED_HTTP_CODES = {400, 429, 500, 502, 503, 504}

# The endpoint Entrez.efetch calls. PMC is fetched directly (#95) because
# Biopython's urlopen has no timeout, so a stalled connection hung the job, and
# its own retries (15 s apart) nested inside entrez_retrieval's.
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# Seconds to connect, then between bytes of the reply.
EFETCH_TIMEOUT = (5, 30)

# NCBI allows 3 requests a second, or 10 with an API key. Biopython's gaps, but
# behind a lock, since concurrent jobs fetch from separate threads.
_efetch_lock = threading.Lock()
_efetch_last = 0.0


def _wait_for_efetch_slot(api_key: str | None) -> None:
    global _efetch_last
    gap = 0.1 if api_key else 0.37
    with _efetch_lock:
        wait = _efetch_last + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _efetch_last = time.monotonic()


def fetch_pmc_xml(pmc_id: str) -> str:
    """
    One efetch of a PMC article, as Entrez.efetch(db="pmc", id=..., retmode="xml")
    makes it: the same parameters and identification, the same text back, and
    urllib's HTTPError for a non-2xx reply, but a bounded wait and one attempt.
    """
    api_key = ncbi_api_key()
    # requests drops a None value, as Biopython does.
    params = {
        "db": "pmc",
        "id": pmc_id,
        "retmode": "xml",
        "tool": Entrez.tool,
        "email": Entrez.email,
        "api_key": api_key,
    }
    _wait_for_efetch_slot(api_key)
    response = requests.get(EFETCH_URL, params=params, timeout=EFETCH_TIMEOUT)
    if not 200 <= response.status_code < 300:
        raise urllib.error.HTTPError(
            response.url, response.status_code, response.reason, response.headers, None
        )
    # Biopython decodes a text/plain reply (and one with no usable Content-Type,
    # which reads as text/plain) through a TextIOWrapper, which also turns \r\n
    # into \n; anything else comes back as bytes that entrez_retrieval decoded.
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type.count("/") != 1 or content_type.split("/")[1] == "plain":
        return io.TextIOWrapper(io.BytesIO(response.content), encoding="UTF-8").read()
    return response.content.decode("utf-8")


class GroundingTool:
    def __init__(self):
        # NCBI's usage policy requires a real contact address on every Entrez
        # request. Checked here, when a run starts, and at server startup.
        Entrez.email = entrez_email()

    def get_uniprot_metadata(self, query_id):
        """
        Searches UniProt for the target protein to extract critical features
        like Transmembrane domains, Signal peptides, and Cellular location.
        Works with UniProt Accessions OR Gene Names.
        """
        clean_query = query_id.replace(">", "").strip()
        print(f"--- [GroundingTool] Searching UniProt for metadata: {clean_query} ---")

        base_url = "https://rest.uniprot.org/uniprotkb/search"

        params = {"query": clean_query, "size": 1}

        try:
            response = requests.get(base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if not data.get("results"):
                print(f"   [GroundingTool] No UniProt match found for {clean_query}")
                return None

            entry = data["results"][0]
            accession = entry.get("primaryAccession", "Unknown")

            # Safe extraction of organism
            organism = "Unknown"
            if "organism" in entry and "scientificName" in entry["organism"]:
                organism = entry["organism"]["scientificName"]

            print(f"   [GroundingTool] Match found: {accession} ({organism})")

            details = {
                "id": accession,
                "organism": organism,
                "features": [],
                "comments": [],
                "keywords": [],
            }

            # 1. Extract Topology & Signal Features from 'features' list
            if "features" in entry:
                for feature in entry["features"]:
                    ft_type = feature.get("type")
                    if ft_type in [
                        "Transmembrane",
                        "Signal",
                        "Intramembrane",
                        "Topological domain",
                    ]:
                        # Extract location safely
                        location = feature.get("location", {})
                        start = location.get("start", {}).get("value", "?")
                        end = location.get("end", {}).get("value", "?")
                        desc = feature.get("description", "")

                        details["features"].append(f"{ft_type} ({start}-{end}): {desc}")

            # 2. Extract Keywords (e.g. "Membrane", "Secreted")
            if "keywords" in entry:
                for kw in entry["keywords"]:
                    kw_name = kw.get("name")
                    if kw_name in ["Membrane", "Secreted", "Cytoplasm", "Cell membrane"]:
                        details["keywords"].append(kw_name)

            # 3. Extract Subcellular Location from 'comments' list
            if "comments" in entry:
                for comment in entry["comments"]:
                    if comment.get("commentType") == "SUBCELLULAR LOCATION":
                        for note in comment.get("subcellularLocations", []):
                            loc_val = note.get("location", {}).get("value")
                            if loc_val:
                                details["comments"].append(loc_val)
            print(f"   [GroundingTool] Metadata: {details}")
            return details

        except Exception as e:
            print(f"   [GroundingTool] UniProt Search Error: {e}")
            return None

    def lookup_citation(self, pdb_id):
        """
        The entry's primary citation as a Paper, from RCSB's /core/pubmed record
        (PMID, PMC ID, DOI and abstract), or None and the hit's status.

        RCSB answers 404 when the entry has no PubMed-indexed publication, which
        is "No PMC Primary Citation" like a paper outside PMC. Any other failure
        is "Citation Lookup Failed": it says nothing about the paper, so #20
        doesn't count it as access dropout.
        """
        try:
            response = requests.get(f"{RCSB_PUBMED_URL}{pdb_id}", timeout=10)
            if response.status_code == 404:
                return None, HitStatus.NO_PMC_PRIMARY_CITATION
            response.raise_for_status()
            record = response.json()
        except Exception as e:
            print(f"   [GroundingTool] RCSB citation lookup failed for {pdb_id}: {e}")
            return None, HitStatus.CITATION_LOOKUP_FAILED

        pmid = (record.get("rcsb_pubmed_container_identifiers") or {}).get("pubmed_id") or (
            record.get("rcsb_id")
        )
        if not pmid:
            return None, HitStatus.NO_PMC_PRIMARY_CITATION

        pmcid = record.get("rcsb_pubmed_central_id")
        return (
            Paper(
                pmid=str(pmid),
                pmcid=pmcid,
                doi=record.get("rcsb_pubmed_doi"),
                abstract=_plain_text(record.get("rcsb_pubmed_abstract_text")),
                access=None if pmcid else PaperAccess.NOT_IN_PMC,
            ),
            None,
        )

    def add_citation_details(self, paper, pdb_id):
        """
        Fill in a paper's title, journal and year from the entry's primary
        citation, for papers the pipeline can't read (#47). The PubMed record
        lookup_citation reads has no title. A failure leaves them unset.
        """
        try:
            response = requests.get(f"{RCSB_ENTRY_URL}{pdb_id}", timeout=10)
            response.raise_for_status()
            citation = response.json().get("rcsb_primary_citation") or {}
        except Exception as e:
            print(f"   [GroundingTool] RCSB citation details failed for {pdb_id}: {e}")
            return
        paper.title = paper.title or _plain_text(citation.get("title"))
        paper.journal = (
            paper.journal or citation.get("rcsb_journal_abbrev") or (citation.get("journal_abbrev"))
        )
        paper.year = paper.year or citation.get("year")

    def entrez_retrieval(self, pmc_id, retries=3, delay=1):
        """
        The article's XML from PMC, or "" when every attempt failed. A failure is
        never an access verdict, whatever its cause, so none escapes as an
        exception for the pipeline to count as "Error Processing".
        """
        for attempt in range(1, retries + 1):
            try:
                return fetch_pmc_xml(pmc_id)
            except urllib.error.HTTPError as e:
                print(f"HTTP {e.code} for {pmc_id}, attempt {attempt}/{retries}")
                if e.code not in RETRIED_HTTP_CODES:
                    break
            except Exception as e:
                print(f"Unexpected error for {pmc_id}, attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(delay * attempt)
        print(f"Failed to retrieve {pmc_id}.")
        return ""

    def search_pmc(self, pmc_id):
        """
        The article's full-text XML and None, or None and the hit's status:
        "No Open Access" when PMC withholds the full text, or "PMC Retrieval
        Failed" when no article came back, which says nothing about access. #20
        counts the two apart, so a flaky fetch doesn't inflate access dropout.

        PMC's withholding notice is matched by its wording, and an article with
        no <body> is withheld too, so a reworded notice still reads as closed
        rather than as an open article with nothing in it.
        """
        pmc_article = self.entrez_retrieval(pmc_id)
        if not pmc_article:
            return None, HitStatus.PMC_RETRIEVAL_FAILED
        if "does not allow downloading" in pmc_article:
            return None, HitStatus.NO_OPEN_ACCESS

        try:
            root = ET.fromstring(pmc_article)
        except ET.ParseError as e:
            print(f"Unreadable reply for {pmc_id}: {e}")
            return None, HitStatus.PMC_RETRIEVAL_FAILED
        article = root if root.tag == "article" else root.find(".//article")
        if article is None:
            # An <error> in place of the article: an unknown or withdrawn ID.
            print(f"No article in the reply for {pmc_id}")
            return None, HitStatus.PMC_RETRIEVAL_FAILED
        if article.find("./body") is None:
            return None, HitStatus.NO_OPEN_ACCESS

        print(f"Article {pmc_id} found through Entrez")
        return pmc_article, None
