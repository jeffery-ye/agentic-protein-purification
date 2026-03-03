import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
from ..agent_tools.protein_similarity_tool import ProteinSimilarityTool
from ..agent_tools.grounding_tool import GroundingTool
from ..agent_tools.methods_tool import MethodsTool
from ..agents.extraction_agent import ExtractionAgent
from ..agents.comprehensive_protocol_agent import SuggestedProtocolAgent
from ..agents.outline_protocol_agent import ProtocolAgent
from ..agent_tools.cttdb_protocols import get_cttdb_info
from ..agent_tools.blast import run_blastp
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from typing import Optional, Callable, List, Dict, Any
import urllib.request
import urllib.error

load_dotenv()

@dataclass
class AgentResult:
    success: bool
    purifications: List[Dict[str, Any]] = field(default_factory=list)
    comprehensive_protocol: Optional[str] = None
    raw_plan: Optional[str] = None
    similar_proteins: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None

class ProteinPurificationAgent:

    def run(self, protein_name, min_pident, min_qcov, max_evalue, max_hits, max_protocols, failed_purification_text: Optional[str] = None, status_callback: Optional[Callable[[str], None]] = None):
        def update_status(msg):
            if status_callback:
                status_callback(msg)

        raw_input = protein_name.strip()
        protein_name_clean = raw_input

        if ">" in raw_input:
            header = raw_input.split('\n')[0]
            protein_name_clean = header.replace(">", "").strip().split()[0]
        
        grounding_tool = GroundingTool()
        target_metadata = grounding_tool.get_uniprot_metadata(protein_name_clean)

        # ID Processing & CTTdb Lookup
        result = self._resolve_input(protein_name, raw_input, update_status)
        if isinstance(result, AgentResult):
            return result
        fasta_id, subject_taxonomy_id, ssgcid_protocol = result
        
        if not fasta_id.startswith(">"):
            update_status(f"Fetching full sequence for UniProt ID: {fasta_id}...")
            full_fasta = self._get_fasta_from_uniprot(fasta_id)
            
            if not full_fasta:
                return AgentResult(success=False, error_message=f"Failed to retrieve sequence for UniProt ID {fasta_id}. Please provide a full FASTA sequence.")
            fasta_id = full_fasta

        # User-provided failed purification overrides CTTdb protocol
        if failed_purification_text and failed_purification_text.strip():
            print("   [Agent] Using user-provided failed purification (overriding CTTdb).")
            update_status(f'Analyzing user-provided failed purification...')
            protocol_agent = ProtocolAgent()
            tabular_protocol = protocol_agent.find_protocol(failed_purification_text.strip())
            
            # Safely handle LLM failure 
            if hasattr(tabular_protocol, 'empty'): 
                protocol_data = tabular_protocol.to_dict(orient="records") if not tabular_protocol.empty else []
            elif isinstance(tabular_protocol, list):
                protocol_data = tabular_protocol
            else:
                protocol_data = []
                
            ssgcid_protocol = {
                'purification_text': failed_purification_text.strip(),
                'protocol': protocol_data,
                'article_title': 'User-Provided Failed Protocol',
                'article_link': None,
                'organism_name': 'User Input',
                'uniprot_id': protein_name
            }

        # Adaptive BLAST
        result = self._run_adaptive_blast(fasta_id, min_qcov, min_pident, max_evalue, max_hits, update_status)
        if isinstance(result, AgentResult):
            return result
        blast_results = result

        # Similarity & Filtering
        update_status(f'Processing {len(blast_results)} BLAST hits...')
        similar_proteins = self._rank_similarities(blast_results, subject_taxonomy_id)

        # Protocol Extraction
        update_status('Finding matching protocols in PMC...')
        purifications = self._find_protocols(similar_proteins, max_protocols, update_status)

        # LLM Synthesis
        comprehensive_protocol, raw_plan = self._synthesize(purifications, ssgcid_protocol, target_metadata, update_status)

        print("   [Agent] Run Complete.")
        
        if ssgcid_protocol:
            purifications.insert(0, ssgcid_protocol)
            
        return AgentResult(
            success=True,
            purifications=purifications,
            comprehensive_protocol=comprehensive_protocol,
            raw_plan=raw_plan,
            similar_proteins=similar_proteins
        )

    def _resolve_input(self, protein_name, raw_input, update_status):
        protocol_agent = ProtocolAgent()
        fasta_id = ""
        subject_taxonomy_id = ""
        ssgcid_protocol = None

        if len(protein_name) < 15 and "." in protein_name:
            print(f"   [Agent] Detected SSGCID ID format: {protein_name}")
            ssgcid_id = protein_name
            update_status(f'Retrieving internal data for {ssgcid_id}...')
            
            try:
                ssgcid_text, sequence, taxonomy_information = get_cttdb_info(ssgcid_id)
                
                if ssgcid_text and sequence:
                    print("   [Agent] CTTdb info found.")
                    try:
                        dna_seq = Seq(sequence.upper())
                        start_index = dna_seq.find("ATG")
                        if start_index != -1:
                            protein_seq = dna_seq[start_index:].translate(to_stop=True)
                            seq_record = SeqRecord(protein_seq, id=ssgcid_id, description="")
                            fasta_id = seq_record.format("fasta")
                        else:
                            print("   [Agent] Warning: No start codon in CTTdb sequence.")
                            return AgentResult(success=False, error_message="No start codon found in internal sequence.")
                    except Exception as e:
                        print(f"   [Agent] Sequence processing error: {e}")
                        return AgentResult(success=False, error_message=f"Sequence Error: {e}")

                    if taxonomy_information:
                        subject_taxonomy_id = taxonomy_information[3]
                        
                        ssgcid_protocol = {
                            'purification_text': ssgcid_text,
                            'protocol': protocol_agent.find_protocol(ssgcid_text),
                            'article_title': f"{ssgcid_id} Failed Protocol ({taxonomy_information[0]} {taxonomy_information[1]})",
                            'article_link': f"https://targetstatus.ssgcid.org/Target/{protein_name}",
                            'organism_name': f"{taxonomy_information[0]} {taxonomy_information[1]}",
                            'uniprot_id': protein_name
                        }
                else:
                    print("   [Agent] No CTTdb entry found. Continuing with name as fasta...")
                    fasta_id = protein_name
            except Exception as e:
                 print(f"   [Agent] CTTdb Lookup failed: {e}")
                 fasta_id = protein_name
        else:
            fasta_id = protein_name

        return fasta_id, subject_taxonomy_id, ssgcid_protocol

    def _run_adaptive_blast(self, fasta_id, min_qcov, min_pident, max_evalue, max_hits, update_status):
        db_path = os.getenv("BLAST_DB_PATH")
        if not db_path:
            return AgentResult(success=False, error_message="BLAST database not configured. See README for setup instructions.")

        update_status(f'Running BLAST (Strict: {min_qcov}% Cov)...')
        try:
            blast_results = run_blastp(fasta_id, min_qcov, min_pident, max_evalue, max_hits, db_path, extra_args=['-seg', 'no'])
        except Exception as e:
            return AgentResult(success=False, error_message=f"BLAST Error: {e}")

        # Fallback: relax parameters if strict search got nothing
        if not blast_results:
            print(f"   [Agent] Strict BLAST returned 0 hits. Attempting Rescue Strategy (Relaxed Params)...")
            update_status('Strict search failed. Relaxing parameters (Rescue Mode)...')
            
            RELAXED_COV = 20.0 
            RELAXED_IDENT = 20.0
            
            try:
                blast_results = run_blastp(fasta_id, RELAXED_COV, RELAXED_IDENT, max_evalue, max_hits, db_path, extra_args=['-seg', 'no'])
                print(f"   [Agent] Rescue Strategy found {len(blast_results)} hits.")
            except Exception as e:
                return AgentResult(success=False, error_message=f"BLAST Rescue Error: {e}")

        if not blast_results:
            print("   [Agent] No hits found even after relaxation.")
            return AgentResult(success=False, error_message="No BLAST results found. The protein might be unique or requires 'nr' database search.")

        return blast_results

    def _rank_similarities(self, blast_results, subject_taxonomy_id):
        protein_similarity_tool = ProteinSimilarityTool()

        if not subject_taxonomy_id: 
            print("   [Agent] Warning: No Taxonomy ID found. Ranking by identity only.")
        
        return protein_similarity_tool.calculate_similarity(subject_taxonomy_id, blast_results)

    def _find_protocols(self, similar_proteins, max_protocols, update_status):
        grounding_tool = GroundingTool()
        methods_tool = MethodsTool()
        extraction_agent = ExtractionAgent()
        protocol_agent = ProtocolAgent()

        purifications = []
        retrieved_ids = []
        
        for i, similar_protein in enumerate(similar_proteins):
            if len(purifications) >= max_protocols:
                break
                
            pdb_id = similar_protein["pdb_id"]
            update_status(f'Analyzing Match {i+1}: {pdb_id} ({similar_protein.get("organism_name", "Unknown")})...')
            
            try:
                # PDB -> PMC -> article -> extract purification -> tabulate
                pmc_id = grounding_tool.query_pdb(pdb_id, retrieved_ids)
                if not pmc_id: continue
                retrieved_ids.append(pmc_id)


                raw_article = grounding_tool.search_pmc(pmc_id)
                if not raw_article: continue


                structured_article = methods_tool.parse_article(raw_article)
                if not structured_article["methods"]: continue


                purification_text = extraction_agent.run(structured_article["methods"], similar_protein.get("protein_name", pdb_id))
                if not purification_text:
                    purification_text = structured_article["methods"] # extraction failed, use raw methods


                tabular_protocol = protocol_agent.find_protocol(purification_text)
                
                if tabular_protocol:
                    print(f"   [Agent] Protocol found for {pdb_id}")
                    protocol_entry = {
                        'purification_text': purification_text,
                        'protocol': tabular_protocol,
                        'article_title': structured_article["title"],
                        'article_link': f"https://pmc.ncbi.nlm.nih.gov/articles/{structured_article['pmcid']}",
                        'organism_name': similar_protein.get("organism_name", "N/A"),
                        'uniprot_id': similar_protein.get("uniprot_id", "N/A")
                    }
                    purifications.append(protocol_entry)
            
            except Exception as e:
                print(f"   [Agent] Error processing {pdb_id}: {e}")
                continue
                
        return purifications

    def _synthesize(self, purifications, ssgcid_protocol, target_metadata, update_status):
        suggestion_agent = SuggestedProtocolAgent()

        if not purifications:
             print("   [Agent] No protocols found. Attempting ab-initio generation from metadata...")
             update_status('No articles found. Generating from Uniprot metadata...')
        else:
             print("   [Agent] Generating final report...")
             update_status('Synthesizing final protocol with LLM...')

        try:
            comprehensive_protocol, raw_plan = suggestion_agent.run(purifications, ssgcid_protocol, target_metadata)
        except Exception as e:
            print(f"   [Agent] LLM Error: {e}")
            comprehensive_protocol = "Error generating the comprehensive protocol."
            raw_plan = "Error generating reasoning."

        return comprehensive_protocol, raw_plan
    
    def _get_fasta_from_uniprot(self, uniprot_id: str) -> Optional[str]:
        """
        Fetches the full FASTA sequence for a given UniProt ID using the UniProt REST API.
        Returns the FASTA string if successful, or None if the ID is invalid/fails.
        """
        uniprot_id = uniprot_id.strip()
        
        url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.fasta"
        
        try:
            # Fetch the data
            with urllib.request.urlopen(url) as response:
                fasta_data = response.read().decode('utf-8')
                return fasta_data
                
        except urllib.error.HTTPError as e:
            print(f"  [UniProt API] HTTP Error {e.code}: Could not fetch sequence for ID {uniprot_id}.")
            return None
        except urllib.error.URLError as e:
            print(f"  [UniProt API] Connection Error: {e.reason}")
            return None
        except Exception as e:
            print(f"  [UniProt API] An unexpected error occurred: {e}")
            return None