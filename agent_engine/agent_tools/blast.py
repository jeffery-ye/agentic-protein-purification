from Bio.Blast import NCBIXML
import sys
import tempfile
import os
import subprocess

def run_blastp(fasta_sequence, min_qcoverage, min_identity, max_evalue, max_hits, db_path, extra_args=None):
    """
    Runs BLASTp locally.
    extra_args: List of additional command line flags (e.g., ['-seg', 'no'])
    """
    print(f"--- [BLAST Tool] Starting BLASTp... ---")
    print(f"    Params: Min Cov={min_qcoverage}%, Min Ident={min_identity}%, E-val={max_evalue}")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.fasta', delete=False) as temp_query:
        temp_query.write(fasta_sequence)
        temp_query_name = temp_query.name
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as temp_output:
        temp_output_name = temp_output.name

    try:
        # Construct command
        blastp_cmd = [
            'blastp',
            '-query', temp_query_name,
            '-db', db_path,
            '-evalue', str(max_evalue),
            '-outfmt', '5',  # XML output
            '-out', temp_output_name,
            '-qcov_hsp_perc', str(min_qcoverage),
            '-max_target_seqs', str(max_hits)
        ]

        if extra_args:
            blastp_cmd.extend(extra_args)

        print(f"    Command: {' '.join(blastp_cmd)}")

        try:
            subprocess.run(blastp_cmd, check=True, stderr=subprocess.PIPE, timeout=30)
        except subprocess.TimeoutExpired as e:
            print(f"  [BLAST Tool] Error: BLASTp timed out after 30 seconds.", file=sys.stderr)
            raise RuntimeError("BLASTp execution timed out. Check database paths and network.") from e
        except subprocess.CalledProcessError as e:
            print(f"BLASTp command failed with exit code {e.returncode}", file=sys.stderr)
            print(e.stderr.decode(), file=sys.stderr)
            raise

        with open(temp_output_name, 'r') as result_handle:
            try:
                blast_records = list(NCBIXML.parse(result_handle))
            except Exception as e:
                print(f"Error parsing BLAST XML: {e}")
                return []
            
            filtered_results = []
            raw_hit_count = 0
            
            for blast_record in blast_records:
                query_length = blast_record.query_length if blast_record.query_length > 0 else 1
                
                for alignment in blast_record.alignments:
                    for hsp in alignment.hsps:
                        raw_hit_count += 1
                        percent_identity = (hsp.identities / hsp.align_length) * 100
                        
                        if percent_identity >= min_identity:
                            accession = alignment.accession
                            pdb_id = accession.split('|')[1] if '|' in accession else accession[0:4]

                            result = {
                                'protein_name': alignment.title,
                                'pdb_id': pdb_id,
                                'length': alignment.length,
                                'e_value': hsp.expect,
                                'pident': percent_identity,
                                'query_coverage': ((hsp.query_end - hsp.query_start + 1) / query_length) * 100,
                                'query_start': hsp.query_start,
                                'query_end': hsp.query_end,
                                'subject_start': hsp.sbjct_start,
                                'subject_end': hsp.sbjct_end
                            }
                            filtered_results.append(result)
            
            print(f"--- [BLAST Tool] Completed. Raw Hits: {raw_hit_count} | Passing Filters: {len(filtered_results)} ---")
            return filtered_results
            
    finally:
        if os.path.exists(temp_query_name):
            os.unlink(temp_query_name)
        if os.path.exists(temp_output_name):
            os.unlink(temp_output_name)