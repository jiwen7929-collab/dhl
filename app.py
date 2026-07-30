import os
import sqlite3
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "records.db"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "123456")

app = FastAPI(title="查岗系统")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT,
            event TEXT,
            timestamp TEXT
        )
    """)
    return conn

@app.on_event("startup")
def startup_event():
    try:
        conn = get_db()
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB init warning:", e)

class ReportBody(BaseModel):
    app_name: str
    event: str

@app.get("/")
async def root():
    return {"status": "ok", "message": "Server is running"}

@app.post("/report")
async def report(body: ReportBody, req: Request):
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(401, "Unauthorized")
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO records (app_name, event, timestamp) VALUES (?, ?, ?)",
        (body.app_name, body.event, now),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/ping")
async def ping():
    return "pong"

@app.get("/activity/summary")
async def summary():
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute(
            "SELECT app_name, event, timestamp FROM records ORDER BY id DESC LIMIT 5"
        )
        recent = cur.fetchall()
        cur.execute(
            "SELECT app_name, event, timestamp FROM records ORDER BY id ASC"
        )
        rows = cur.fetchall()
        conn.close()

        sessions, opens = {}, {}
        for r in rows:
            app, ev, ts = r
            if ev == "open":
                opens[app] = datetime.fromisoformat(ts)
            elif ev == "close" and app in opens:
                gap = int(
                    (datetime.fromisoformat(ts) - opens[app]).total_seconds()
                )
                sessions[app] = sessions.get(app, 0) + gap
                del opens[app]
        return {"recent_apps": [r[0] for r in recent], "sessions": sessions}
    except Exception as e:
        return {"recent_apps": [], "sessions": {}, "error": str(e)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
