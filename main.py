"""FastAPI webhook server.

GET  /webhook  -> Meta's one-time verification handshake
POST /webhook  -> incoming WhatsApp messages (and delivery receipts)
"""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

import config
import db
import llm
import whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

UNSUPPORTED_REPLY = (
    "Sorry, I can only read text messages right now 🙏 Please type your message.\n"
    "Maaf kijiye, main abhi sirf text messages samajh paata hoon. Kripya type karke bhejein."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    db.init_db()
    if not config.APP_SECRET:
        log.warning("APP_SECRET not set: webhook signatures are NOT being verified (ok for local dev only)")
    yield


app = FastAPI(title="Salon WhatsApp Assistant", lifespan=lifespan)


@app.get("/")
def health():
    return {"status": "ok"}


# ---------- 1. Webhook verification (GET) ----------
@app.get("/webhook")
def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """When you click 'Verify and save' in Meta's dashboard, Meta calls this URL with three
    query params. We must (a) check the token matches the one we chose and (b) echo back
    hub.challenge as plain text. Anything else and Meta refuses to save the webhook.
    """
    if mode == "subscribe" and token == config.VERIFY_TOKEN and challenge:
        log.info("Webhook verified by Meta")
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ---------- 2. Incoming messages (POST) ----------
@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """Meta expects a 200 within a few seconds or it retries (causing duplicate replies).
    The LLM can be slower than that, so we ack immediately and do the work in the background.
    """
    raw_body = await request.body()  # raw bytes are needed for the signature check

    if not whatsapp.verify_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Bad signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    for msg in whatsapp.parse_incoming_messages(payload):
        if not db.mark_message_processed(msg.message_id):
            log.info("Duplicate delivery of %s ignored", msg.message_id)
            continue
        background_tasks.add_task(handle_message, msg)  # sync function -> runs in a thread pool

    return {"status": "received"}


def handle_message(msg: whatsapp.IncomingMessage) -> None:
    """Runs after the webhook has already returned 200."""
    log.info("Incoming %s from %s", msg.type, msg.wa_id)

    if msg.type == "reaction":  # a 👍 on one of our messages: nothing to answer
        return

    whatsapp.mark_as_read(msg.message_id)

    if msg.type != "text" or not msg.text.strip():
        db.log_message(msg.wa_id, "user", f"[{msg.type} message]")
        reply = UNSUPPORTED_REPLY
        db.log_message(msg.wa_id, "assistant", reply)
    else:
        try:
            reply = llm.generate_reply(msg.wa_id, msg.text, msg.profile_name)
        except Exception:  # noqa: BLE001 - never leave the customer on read
            log.exception("LLM pipeline failed for %s", msg.wa_id)
            reply = llm.FALLBACK_REPLY
            db.log_message(msg.wa_id, "assistant", reply, tool_name="error")

    try:
        whatsapp.send_text(msg.wa_id, reply)
    except Exception:  # noqa: BLE001 - already logged inside send_text
        log.exception("Failed to send reply to %s", msg.wa_id)
