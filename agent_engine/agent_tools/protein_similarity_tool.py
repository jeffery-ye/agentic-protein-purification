# Run neo4J local server (Windows CMD line)
# set NEO4J_HOME=C:\PATH\TO\YOUR\DB
# %NEO4J_HOME%\bin\neo4j console

import os
from dotenv import load_dotenv
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from neo4j import GraphDatabase
import time

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

def create_retry_session(retries=3, backoff_factor=1, status_forcelist=(500, 502, 504)):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

class ProteinSimilarityTool:
    
    def __init__(self):
        self.driver = self.create_driver()
        self.http_session = create_retry_session()

    # Calculates taxonomic distance -- penalizing for traversing higher nodes
    def calculate_similarity(self, starting_id, blast_results):
        print(f"--- [SimilarityTool] Calculating similarity for {len(blast_results)} BLAST hits relative to TaxID: {starting_id} ---")
        
        uniprot_url = "https://data.rcsb.org/rest/v1/core/uniprot/"
        similar_proteins = []
        
        try:
            for i, blast_result in enumerate(blast_results):
                pdb_id = blast_result["pdb_id"]
                
                if i % 10 == 0 and i > 0:
                    time.sleep(1)

                # Get UniProt data from the PDB
                try:
                    response = self.http_session.get(f"{uniprot_url}{pdb_id}/1", timeout=10)
                    response.raise_for_status()
                    uniprot_data = response.json()
                except requests.exceptions.RequestException as e:
                    print(f"[SimilarityTool] Warning: HTTP error for PDB {pdb_id}: {e}")
                    blast_result["similarity_score"] = blast_result["pident"] / 100
                    similar_proteins.append(blast_result)
                    continue
                except Exception as e:
                    print(f"[SimilarityTool] Warning: Unexpected error for PDB {pdb_id}: {e}")
                    blast_result["similarity_score"] = blast_result["pident"] / 100
                    similar_proteins.append(blast_result)
                    continue

                if uniprot_data and uniprot_data[0] is not None:
                    protein_info = uniprot_data[0]

                    if "rcsb_uniprot_container_identifiers" in protein_info:
                        blast_result["uniprot_id"] = protein_info["rcsb_uniprot_container_identifiers"]["uniprot_id"]

                    if "rcsb_uniprot_protein" in protein_info:
                        source_organism = protein_info["rcsb_uniprot_protein"].get("source_organism")
                        if source_organism:
                            blast_result["organism_name"] = source_organism.get("scientific_name")
                            blast_result["taxonomy_id"] = source_organism.get("taxonomy_id")
                            
                            if starting_id and self.driver:
                                print(f"[SimilarityTool] Checking taxonomy for {pdb_id} (TaxID: {blast_result['taxonomy_id']})...")
                                tax_dist_score = self.neo4j_search(
                                    starting_id, 
                                    blast_result["taxonomy_id"], 
                                    blast_result["uniprot_id"]
                                )
                                blast_result["similarity_score"] = (blast_result["pident"]/100 * 0.5) + (tax_dist_score * 0.5)
                                print(f"   -> Score: {blast_result['similarity_score']:.3f} (Pident: {blast_result['pident']}%, TaxScore: {tax_dist_score})")
                            else:
                                blast_result["similarity_score"] = blast_result["pident"] / 100
                                print(f"   -> Score: {blast_result['similarity_score']:.3f} (Pident only, no taxonomy data)")
                    similar_proteins.append(blast_result)
        finally:
            if self.driver:
                self.driver.close()
                
        print(f"--- [SimilarityTool] Finished. Found {len(similar_proteins)} valid candidates. ---")
        return sorted(similar_proteins, key=lambda item: item["similarity_score"], reverse=True)
                    
    def neo4j_search(self, starting_id, taxonomy_id, uniprot_id):
        """
        Searches Neo4j for shortest distance between two taxa.
        """
        score = 0
        
        # Performs the neo4j query
        try:
            records, summary, _ = self._execute_taxonomy_query(starting_id, taxonomy_id)
        except Exception as e:
            print(f"[SimilarityTool] Neo4j Query Error for TaxID {taxonomy_id}: {e}")
            return 0.5 # Default fallback
        
        # If not found, try parents
        if not records:
            print(f"   [Neo4j] Direct path not found for {taxonomy_id}. Trying lineage...")
            
            parent_taxon_ids = self._get_uniprot_lineage(uniprot_id)
            
            for parent_id in parent_taxon_ids:
                try:
                    records, summary, _ = self._execute_taxonomy_query(starting_id, parent_id)
                    score += 1 # Penalty for moving up the tree
                    if records:
                        print(f"   [Neo4j] Match found at parent TaxID: {parent_id} (Steps up: {score})")
                        break
                except Exception:
                    continue
        
        if records:
            return self._calculate_normalized_score(records, summary, score)
        else:
            print("   [Neo4j] No valid taxonomic path found.")
            return 0.5
    
    def _get_uniprot_lineage(self, uniprot_id):
        """
        Fetches taxonomic lineage from UniProt API.
        """
        base_url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}"
        params = {"fields": "lineage_ids"}
        headers = {"accept": "application/json"}
        
        try:
            response = self.http_session.get(base_url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            uniprot_data = response.json()
            
            lineage_data = uniprot_data.get('lineages', [])
            taxon_ids = [item['taxonId'] for item in lineage_data]
            
            taxon_ids.reverse()
            return taxon_ids
            
        except requests.exceptions.RequestException as e:
            print(f"[SimilarityTool] Error fetching UniProt lineage for {uniprot_id}: {e}")
            return []

    def _execute_taxonomy_query(self, starting_id, taxon_id):
        query = '''
        MATCH (input_taxon:Taxon {taxonId: $input_taxon}), (pubmed_taxon:Taxon {taxonId: $pubmed_taxon})
        MATCH p = (input_taxon)-[:BELONGS_TO*0..]-(pubmed_taxon)
        RETURN [x IN nodes(p) | {taxonID: x.taxonId, name: x.name, rank: x.rank}] AS result
        ORDER BY length(p) ASC
        LIMIT 1
        '''
        parameters = {
            "input_taxon": str(starting_id), # Ensure string/int consistency based on your DB schema
            "pubmed_taxon": str(taxon_id)
        }
        if not self.driver:
            return [], None, None
        return self.driver.execute_query(query, parameters_=parameters)

    def _calculate_normalized_score(self, records, summary, total_weight):
        # Weights penalize steps in the taxonomy tree
        taxonomic_weights = {
            "species": 1, "genus": 2, "family": 3, "order": 4,
            "class": 5, "phylum": 6, "kingdom": 7, "domain": 8,
        }
        
        record_data = records[0].data()['result']
        
        for taxon in record_data:
            rank = taxon.get('rank')
            if rank in taxonomic_weights:
                total_weight += taxonomic_weights[rank]
                
        max_weight = sum(taxonomic_weights.values()) * 2
        normalized_score = 1 - (total_weight / max_weight)
        
        if normalized_score == 1:
            normalized_score /= 2 
            
        return normalized_score
        
    def create_driver(self):
        if not all([NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD]):
            print("[SimilarityTool] Neo4j not configured, scoring by BLAST identity only.")
            return None
        try:
            driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            driver.verify_connectivity()
            print("[SimilarityTool] Neo4j connection established.")
            return driver
        except Exception as e:
            print(f"[SimilarityTool] Neo4j unavailable ({e}), scoring by BLAST identity only.")
            return None