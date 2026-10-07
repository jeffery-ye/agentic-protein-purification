"""
Theoretical pI and molecular weight of the target, computed from its sequence (#81).

The planner used to estimate both itself, and its pI heuristic (ADR-0004)
rested on that guess. These are values for the full sequence as given: tags,
signal peptides and cleavage all change them, and the planner is told so.
"""

from typing import Optional

from Bio.SeqUtils.ProtParam import ProteinAnalysis


def sequence_properties(fasta: str) -> Optional[dict]:
    """
    {"theoretical_pi", "molecular_weight_da"} for the first record in `fasta`,
    or None when it has no sequence or one ProtParam can't analyse (ambiguous
    residues such as X or B). A missing value never fails the job.
    """
    lines = fasta.strip().splitlines()
    if not lines or not lines[0].startswith(">"):
        return None

    residues = []
    for line in lines[1:]:
        if line.startswith(">"):
            break
        residues.append("".join(line.split()))
    sequence = "".join(residues).upper().rstrip("*")
    if not sequence:
        return None

    try:
        analysis = ProteinAnalysis(sequence)
        return {
            "theoretical_pi": round(analysis.isoelectric_point(), 2),
            "molecular_weight_da": round(analysis.molecular_weight()),
        }
    except (ValueError, KeyError) as e:
        print(f"   [SequenceProperties] Could not analyse the target sequence: {e}")
        return None
