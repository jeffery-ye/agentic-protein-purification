import requests
import time
from Bio import Entrez
import urllib.error
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Entrez.email = "your@email.com"

class GroundingTool:
    
    def get_uniprot_metadata(self, query_id):
        """
        Searches UniProt for the target protein to extract critical features
        like Transmembrane domains, Signal peptides, and Cellular location.
        Works with UniProt Accessions OR Gene Names.
        """
        clean_query = query_id.replace(">", "").strip()
        print(f"--- [GroundingTool] Searching UniProt for metadata: {clean_query} ---")
        
        base_url = "https://rest.uniprot.org/uniprotkb/search"
        
        params = {
            "query": clean_query,
            "size": 1  
        }
        
        try:
            response = requests.get(base_url, params=params, timeout=10, verify=False)
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
                "keywords": []
            }

            # 1. Extract Topology & Signal Features from 'features' list
            if "features" in entry:
                for feature in entry["features"]:
                    ft_type = feature.get("type")
                    if ft_type in ["Transmembrane", "Signal", "Intramembrane", "Topological domain"]:
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
            print(details)
            return details

        except Exception as e:
            print(f"   [GroundingTool] UniProt Search Error: {e}")
            return None
        
    def query_pdb(self, pdb_id, retrieved_ids):
        """
        This tool uses the PDB and Entrez API to query information about a protein and find articles related to it
        Parameters:
            pdb_id: this is the 4 character id that identifies a protein in the PDB
        """
        base_url = "https://data.rcsb.org/rest/v1/core/pubmed/"
        
        try:
            # Get PMC ID from the pdb API
            response = requests.get(f"{base_url}{pdb_id}", timeout=10, verify=False)
            response.raise_for_status()

            pmc_id = response.json()["rcsb_pubmed_central_id"]

            if pmc_id and pmc_id not in retrieved_ids:
                return pmc_id
            
            else:
                return None
            
            
        except Exception as e:
            print(f"No PMC Article for {pdb_id}. {e}")
        return None
    
    # downloads pubmed central paper using Entrez
    def entrez_retrieval(self, pmc_id, retries=3, delay=1):
        for attempt in range(retries):
            try:
                handle = Entrez.efetch(db="pmc", id=pmc_id, retmode="xml")
                data = handle.read().decode('utf-8')
                return data
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    print(f"HTTP 400 for {pmc_id}, attempt {attempt+1}/{retries}")
                    time.sleep(delay)
                else:
                    raise
            except Exception as e:
                print(f"Unexpected error for {pmc_id}: {e}")
                time.sleep(delay)
        print(f"Failed to retrieve {pmc_id} after {retries} attempts.")
        return ""

    def search_pmc(self, pmc_id):
        # Tries to retrieve through Entrez
        pmc_article = self.entrez_retrieval(pmc_id)
        
        # Triggers if full-text download is allowed
        if pmc_article and "does not allow downloading" not in pmc_article:
            print(f"Article {pmc_id} found through Entrez")
            return pmc_article
            
        return None
