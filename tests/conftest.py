"""
Shared test configuration.

The pipeline checks ENTREZ_EMAIL when a run starts, so tests that run it need
a dummy value. `setdefault` keeps a real local `.env` working if one is present. No LLM credential is needed: the model is
resolved on first use, and the tests never make an LLM call.

Every test gets its own DATA_DIR under pytest's tmp directory, so nothing ever
writes the job database into the repository's data/ directory. Deploy mode
won't start without DAILY_JOB_CAP (#26) or an explicit AUTH_ENABLED, so they
default to a generous cap and sign-in off; test_auth.py clears AUTH_ENABLED.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("ENTREZ_EMAIL", "test@example.org")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def data_dir(tmp_path_factory, monkeypatch) -> Path:
    path = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("DATA_DIR", str(path))
    monkeypatch.setenv("DAILY_JOB_CAP", "1000")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    # Drop any job repository a previous test opened in main.py.
    main = sys.modules.get("main")
    if main is not None:
        monkeypatch.setattr(main, "_job_repo", None)
    return path


@pytest.fixture
def pmc_article() -> str:
    return (FIXTURES / "pmc_article.xml").read_text(encoding="utf-8")


@pytest.fixture
def blast_output() -> str:
    return (FIXTURES / "blast_output.xml").read_text(encoding="utf-8")
