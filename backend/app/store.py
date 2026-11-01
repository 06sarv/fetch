import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

class JobStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.db = directory / "fetch.sqlite3"
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, objective TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL)""")

    def connect(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def save(self, job: dict[str, Any]):
        with self.connect() as c:
            c.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, data=excluded.data",
                      (job["id"], job["status"], job["objective"], job["created_at"], job["updated_at"], json.dumps(job)))

    def get(self, job_id: str):
        with self.connect() as c:
            row = c.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list(self):
        with self.connect() as c:
            rows = c.execute("SELECT data FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
        return [json.loads(r["data"]) for r in rows]

def now_iso():
    return datetime.now(timezone.utc).isoformat()
