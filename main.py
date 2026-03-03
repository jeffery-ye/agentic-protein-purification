import uuid
import sys
import os
from threading import Lock
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from agent_engine.agents.agent_body import ProteinPurificationAgent, AgentResult
from schemas import PurificationRequest, ProtocolResult

job_store = {}
job_lock = Lock()

def run_agent_task(job_id: str, request: PurificationRequest):
    """
    Wraps the synchronous agent in a function compatible with FastAPI BackgroundTasks.
    """
    
    def status_callback(message: str):
        with job_lock:
            if job_id in job_store:
                job_store[job_id]["status"] = message

    try:
        with job_lock:
            job_store[job_id] = {"status": "Initializing Agent...", "data": None, "error": None}
        
        agent = ProteinPurificationAgent()
        
        result: AgentResult = agent.run(
            protein_name=request.fasta_id,
            min_pident=request.min_percent_identity,
            min_qcov=request.min_query_coverage,
            max_evalue=request.max_evalue,
            max_hits=request.max_hits,
            max_protocols=5,
            failed_purification_text=request.failed_purification_text,
            status_callback=status_callback
        )

        if not result.success:
            with job_lock:
                job_store[job_id]["status"] = "ERROR"
                job_store[job_id]["error"] = result.error_message
            return

        structured_result = ProtocolResult(
            purifications=result.purifications,
            comprehensive_protocol=result.comprehensive_protocol,
            raw_plan=result.raw_plan,
            blast_results=result.similar_proteins,
            error_message=None
        )
        
        with job_lock:
            job_store[job_id]["status"] = "COMPLETED"
            job_store[job_id]["data"] = structured_result

    except Exception as e:
        with job_lock:
            job_store[job_id]["status"] = "ERROR"
            job_store[job_id]["error"] = str(e)

app = FastAPI(title="Purification Rescue Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/analyze", response_model=dict)
async def start_analysis(request: PurificationRequest, background_tasks: BackgroundTasks):
    """
    Starts the agent in the background and returns a Job ID immediately.
    """
    job_id = str(uuid.uuid4())
    
    background_tasks.add_task(run_agent_task, job_id, request)
    
    return {"job_id": job_id, "status": "PENDING"}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    """
    Polled by the frontend to check progress.
    Returns specific status messages (e.g. "Running BLAST...") or "COMPLETED".
    """
    with job_lock:
        job = job_store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        response = {
            "job_id": job_id,
            "status": job["status"]
        }
        
        if job["status"] == "ERROR":
            response["error"] = job.get("error")
        
    return response

@app.get("/result/{job_id}", response_model=ProtocolResult)
async def get_result(job_id: str):
    """
    Retrieves the final detailed report.
    """
    with job_lock:
        job = job_store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
            
        if job["status"] == "ERROR":
            raise HTTPException(status_code=400, detail=f"Job failed: {job.get('error')}")
            
        if job["status"] != "COMPLETED":
            raise HTTPException(status_code=202, detail="Result not ready yet")
            
        result = job["data"]
    return result