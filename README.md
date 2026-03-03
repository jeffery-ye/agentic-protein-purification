# Purification Protocol Agent

The associated paper is available to read at [link coming soon]. This application was developed at Seattle Children’s Research Institute (SCRI) which is part of Seattle Children’s Hospital (SCH) by Jeffery Ye

This application is an AI-driven system that creates and optimizes recombinant protein purification protocols, grounded by primary citations for analogous proteins. It uses multi-agent LLM workflows and bioinformatics tools to analyze protein metadata, automatically mine literature, and generate protocols based on best practices.

## Prerequisites

- **Python:** Python 3.10, 3.11, or 3.12 is required. (Python 3.13+ may fail to install dependencies).
- **BLAST+**: Install the command-line tools from [NCBI](https://blast.ncbi.nlm.nih.gov/doc/blast-help/downloadblastdata.html) and ensure they are added to your system's `PATH`.
- **pdbaa Database**: Download the pre-formatted database from the [NCBI FTP Site](https://ftp.ncbi.nlm.nih.gov/blast/db/).
- **Optional Dependencies**: SSGCID database and Neo4j graph database are optional. The application will function without them.

## Configuration

1. Create a `.env` file in the root directory (based on .env.example) and set your `BLAST_DB_PATH`. Note: This path must point directly to the `pdbaa` file inside your extracted folder (e.g., `C:\db\pdbaa\pdbaa`).
2. Set your NCBI Entrez email address in `agent_engine/agent_tools/grounding_tool.py` to comply with NCBI API guidelines.

## Running the Application

You will need two terminal windows to run the backend and frontend.

### Backend
```bash
pip install -r requirements.txt
fastapi dev main.py
```

### Frontend
```bash
cd purification-rescue-frontend
npm install
npm run dev
```

## Tech Stack
- **Backend**: FastAPI, PydanticAI (LLM Agents)
- **Frontend**: Svelte + Vite
- **Other Tools & Technologies**: Neo4j Graph Database, PostgreSQL + psycopg2 for programmatic access, API calls to various bioinformatics tools like BLAST+ and UniProt