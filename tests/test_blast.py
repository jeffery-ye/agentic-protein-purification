"""
Tests for run_blastp.

The `blastp` binary is replaced with a stub that writes a canned XML report to
the `-out` path the real command would have used, so the XML parsing and
filtering logic is exercised without BLAST+ or a pdbaa database installed.
"""

import os
import subprocess

import pytest

from agent_engine.agent_tools import blast


@pytest.fixture
def fake_blastp(monkeypatch, blast_output):
    """Replace subprocess.run with a stub that emits the fixture report."""
    calls = {}

    def _run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["query_path"] = cmd[cmd.index("-query") + 1]
        calls["out_path"] = cmd[cmd.index("-out") + 1]
        with open(calls["out_path"], "w", encoding="utf-8") as handle:
            handle.write(blast_output)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(blast.subprocess, "run", _run)
    return calls


def run(min_identity=30.0, **kwargs):
    params = dict(
        fasta_sequence=">q\nMKAW",
        min_qcoverage=0,
        min_identity=min_identity,
        max_evalue=10,
        max_hits=50,
        db_path="db",
    )
    params.update(kwargs)
    return blast.run_blastp(**params)


def test_parses_hits_from_xml(fake_blastp):
    results = run()

    assert [r.pdb_id for r in results] == ["1ABC", "2DEF"]
    assert results[0].length == 120
    assert results[0].e_value == pytest.approx(1e-50)


def test_percent_identity_computed_from_alignment(fake_blastp):
    """pident is identities/align_len, not a value BLAST reports directly."""
    results = run()

    assert results[0].pident == pytest.approx(95.0)  # 95 / 100
    assert results[1].pident == pytest.approx(80.0)  # 40 / 50


def test_identity_threshold_filters_hsps(fake_blastp):
    """The fixture's second HSP on hit 1 is 10/50 = 20% identity."""
    assert len(run(min_identity=30.0)) == 2

    kept = run(min_identity=10.0)
    assert len(kept) == 3
    assert min(r.pident for r in kept) == pytest.approx(20.0)

    high = run(min_identity=90.0)
    assert [r.pdb_id for r in high] == ["1ABC"]

    assert run(min_identity=99.0) == []


def test_query_coverage_uses_query_span(fake_blastp):
    results = run()

    assert results[0].query_coverage == pytest.approx(100.0)  # 1..100 of 100
    assert results[1].query_coverage == pytest.approx(50.0)  # 11..60 of 100


def test_pdb_id_derived_from_accession(fake_blastp):
    """Piped accessions take the second field; bare ones take four characters."""
    results = run()

    assert results[0].pdb_id == "1ABC"  # from "pdb|1ABC|A"
    assert results[1].pdb_id == "2DEF"  # from "2DEFXYZ"


def test_extra_args_are_appended_to_command(fake_blastp):
    run(extra_args=["-seg", "no"])

    assert fake_blastp["cmd"][-2:] == ["-seg", "no"]


def test_temp_files_are_cleaned_up(fake_blastp):
    run()

    assert not os.path.exists(fake_blastp["query_path"])
    assert not os.path.exists(fake_blastp["out_path"])


def test_timeout_raises_a_blast_error(monkeypatch):
    def _timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(blast.subprocess, "run", _timeout)

    with pytest.raises(blast.BlastError, match="timed out"):
        run()


def test_a_nonzero_exit_raises_a_blast_error_without_server_paths(monkeypatch):
    """The message reaches the user, so the command line and stderr stay in the log."""

    def _fail(cmd, **kwargs):
        raise subprocess.CalledProcessError(2, cmd, stderr=b"BLAST Database error: /db/pdbaa")

    monkeypatch.setattr(blast.subprocess, "run", _fail)

    with pytest.raises(blast.BlastError) as raised:
        run()

    assert "/db/" not in str(raised.value) and "-query" not in str(raised.value)


def test_unparseable_output_returns_empty(monkeypatch):
    def _garbage(cmd, **kwargs):
        with open(cmd[cmd.index("-out") + 1], "w", encoding="utf-8") as handle:
            handle.write("not xml at all")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(blast.subprocess, "run", _garbage)

    assert run() == []
