import os
import sqlite3
import requests
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "records.db"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "123456")
BARK_KEY = os.environ.get("BARK_API_KEY", "")

app = FastAPI(title="查岗与MCP二合一系统")
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

# ================= MCP 服务模块 =================

def check_on_wife(limit=10):
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

        apps = [r[0] for r in recent]
        lines = [f"最近打开:{', '.join(apps)}" if apps else "暂无记录"]
        if sessions:
            for app_name, secs in sorted(
                sessions.items(), key=lambda x: x[1], reverse=True
            ):
                m, s = divmod(secs, 60)
                lines.append(f" {app_name}: {m}分{s}秒")
        return "\n".join(lines)
    except Exception as e:
        return f"查岗失败:{e}"

def bark_alert(title="查岗警告", content=""):
    if not content:
        return "内容不能为空"
    if not BARK_KEY:
        return "未配置 BARK_API_KEY 环境变量"
    url = f"https://api.day.app/{BARK_KEY}/{title}/{content}"
    try:
        r = requests.get(url, timeout=10)
        return "推送成功" if r.status_code == 200 else "推送失败"
    except Exception as e:
        return f"推送异常:{e}"

TOOLS = [
    {
        "name": "check_on_wife",
        "description": "查岗老婆的手机活动，获取最近打开的APP和使用时长",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
    },
    {
        "name": "bark_alert",
        "description": "给老婆手机发送突袭弹窗推送通知",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["content"],
        },
    },
]

FUNCS = {"check_on_wife": check_on_wife, "bark_alert": bark_alert}

@app.post("/mcp")
async def mcp(req: Request):
    body = await req.json()
    method, params = body.get("method"), body.get("params") or {}
    rid = body.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "查岗MCP", "version": "1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in FUNCS:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "未知工具"},
            }
        result = FUNCS[name](**args)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": str(result)}]},
        }

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"未知方法:{method}"},
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

