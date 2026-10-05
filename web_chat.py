"""Local web chat UI — same bot brain as the WhatsApp version, but runs in a browser.

Run:
    uvicorn web_chat:app --reload --port 8000

Open:
    http://127.0.0.1:8000

Uses a separate database (web_test.db) so it doesn't pollute your real logs.
"""
import os

os.environ["DB_PATH"] = "web_test.db"  # must be set before importing config

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

import config
import db
import llm

if not config.OPENROUTER_API_KEY:
    raise SystemExit("Set OPENROUTER_API_KEY in your .env first.")

db.init_db()

app = FastAPI(title="Salon Chat (Local)")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Unique ID per browser session; simplest is to use a fixed ID for local demo.
SESSION_ID = "local-web-user"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="chat.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.post("/chat")
async def chat(payload: dict):
    text = (payload.get("message") or "").strip()
    if not text:
        return {"reply": ""}
    try:
        reply = llm.generate_reply(SESSION_ID, text, "Web User")
    except Exception as e:
        print(f"[web_chat] error: {e}")
        reply = "Sorry, something went wrong. Please try again."
    return {"reply": reply}