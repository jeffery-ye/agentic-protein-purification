#!/usr/bin/env bash
# Copies the job database to a second file on the data volume (#24) with
# SQLite's online backup, half an hour before the daily snapshot. A snapshot
# can catch the live database mid-write; the copy is always consistent.
set -euo pipefail
cd "${PPR_DIR:-/opt/ppr}"

docker compose exec -T app python - <<'PY'
import os
import sqlite3

source = sqlite3.connect("/data/jobs.sqlite3")
copy = sqlite3.connect("/data/jobs.backup.sqlite3.tmp")
with copy:
    source.backup(copy)
copy.close()
source.close()
os.replace("/data/jobs.backup.sqlite3.tmp", "/data/jobs.backup.sqlite3")
PY
