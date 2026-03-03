from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class PurificationRequest(BaseModel):
    fasta_id: str = Field(..., description="Protein FASTA sequence or ID")
    failed_purification_text: Optional[str] = Field(default=None, description="User-provided failed purification text to override CTTdb lookup")
    min_percent_identity: float = Field(default=90.0, ge=0, le=100)
    min_query_coverage: float = Field(default=90.0, ge=0, le=100)
    max_evalue: float = Field(default=1e-5)
    max_hits: int = Field(default=10, ge=1)

class ProtocolResult(BaseModel):
    purifications: Optional[List[Dict[str, Any]]] = None
    comprehensive_protocol: Optional[str] = None
    raw_plan: Optional[str] = None
    blast_results: Optional[List[Dict[str, Any]]] = None
    error_message: Optional[str] = None