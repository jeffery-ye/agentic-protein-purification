export interface PurificationRequest {
    fasta_id: string;
    failed_purification_text?: string | null;
    min_percent_identity?: number | null; 
    min_query_coverage?: number | null;   
    max_evalue?: number | null;           
    max_hits?: number | null;
}

export interface BufferStep {
    purification_step: string;
    buffer_name?: string | null;
    buffer_composition?: string | null;
    ph?: number | null;
    salt_type?: string | null;
    buffer_supplement?: string | null;
}

export interface Purification {
    purification_text?: string | null;
    article_title?: string | null;
    article_link?: string | null;
    protein_name?: string | null;
    organism_name?: string | null;
    uniprot_id?: string | null;
    protocol?: BufferStep[] | null;
}

export interface BlastResult {
    protein_name: string;
    pdb_id: string;
    pident: number;
    e_value: number;
    query_coverage: number;
    similarity_score?: number | null;
    taxonomy_id?: string | null;
    query_start?: number | null;
    query_end?: number | null;
}

export interface ProtocolResult {
    purifications?: Purification[] | null;
    comprehensive_protocol?: string | null;
    raw_plan?: string | null;
    blast_results?: BlastResult[] | null;
    error_message?: string | null;
}

export type JobStatus = 'PENDING' | 'COMPLETED' | 'ERROR' | string;
