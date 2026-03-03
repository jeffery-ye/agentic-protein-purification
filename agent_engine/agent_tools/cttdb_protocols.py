import os
import pyodbc
from dotenv import load_dotenv

load_dotenv()

DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_DRIVER = os.getenv("DB_DRIVER", "{ODBC Driver 17 for SQL Server}") 

# RealProcessIDs for standard purification/refolding workflows in SSGCID pipeline
PURIFICATION_PROCESS_IDS = (190, 191, 192, 193, 194, 241, 267, 280, 304)

def get_cttdb_info(protein_id: str):
    """
    Retrieves protocol text for a given protein ID from a SQL Server database.
    Returns: (protocol_text, sequence, taxonomy_row)
    """
    print(f"--- [CTTdb] Starting lookup for SSGCID ID: {protein_id} ---")
    
    protocol_text = None
    sequence = None
    taxonomy = None
    conn = None
    
    protocol_query = """
        SELECT TOP (1)
            p.ProtocolText
        FROM
            Protocol AS p
        JOIN
            RealProcessInstance AS rpi ON p.ProtocolID = rpi.ProtocolID
        JOIN
            Construct AS c ON rpi.DataLinkID = c.ConstructID
        WHERE
            c.SSGCIDID LIKE ? AND rpi.RealProcessID IN (?,?,?,?,?,?,?,?,?) AND p.ProtocolText IS NOT NULL;
    """
    
    sequence_query = """
        SELECT TOP (1) NTSeq
        FROM Construct
        WHERE SSGCIDID LIKE ?
    """
    
    taxonomy_query = """
        SELECT Genus, Species, Strain, TaxonomyID
        FROM Organism
        WHERE Code LIKE ?
    """

    try:
        conn_str = (
            f"DRIVER={DB_DRIVER};"
            f"SERVER={DB_SERVER};"
            f"DATABASE={DB_NAME};"
            "Trusted_Connection=yes;" 
        )
        
        print(f"--- [CTTdb] Connecting to server: {DB_SERVER} ---")
        conn = pyodbc.connect(conn_str)
        
        with conn.cursor() as cur:
            search_term = f"{protein_id.upper()}%"
            # Extract the organism code (e.g. 'MytuD' from 'MytuD.00516.a')
            taxa_code = search_term[0:5]

            # 1. Fetch Protocol
            params = (search_term, *PURIFICATION_PROCESS_IDS)
            print(f"--- [CTTdb] Executing Protocol Query for {search_term} ---")
            cur.execute(protocol_query, params)
            result = cur.fetchall()
            
            if result:
                protocol_text = result[0][0]
                print(f"--- [CTTdb] Protocol FOUND. Length: {len(protocol_text)} chars ---")
            else:
                print(f"--- [CTTdb] No protocol found for {protein_id} ---")
            
            # 2. Fetch Sequence
            print(f"--- [CTTdb] Executing Sequence Query ---")
            cur.execute(sequence_query, (search_term,))
            sequences = cur.fetchall()
            
            if sequences:
                sequence = sequences[0][0]
                print(f"--- [CTTdb] Sequence FOUND. Length: {len(sequence)} chars ---")
            else:
                print("--- [CTTdb] No sequence found ---")

            # 3. Fetch Taxonomy
            print(f"--- [CTTdb] Executing Taxonomy Query for code: {taxa_code} ---")
            cur.execute(taxonomy_query, (taxa_code,))
            taxonomy_rows = cur.fetchall()
            
            if taxonomy_rows:
                taxonomy = taxonomy_rows[0]
                print(f"--- [CTTdb] Taxonomy FOUND: {taxonomy} ---")
            else:
                print(f"--- [CTTdb] No taxonomy found for code {taxa_code} ---")

    except pyodbc.Error as e:
        print(f"--- [CTTdb] DATABASE ERROR: {e} ---")
        return None, None, None
        
    finally:
        if conn:
            conn.close()
            print("--- [CTTdb] Connection closed ---")
            
    return protocol_text, sequence, taxonomy