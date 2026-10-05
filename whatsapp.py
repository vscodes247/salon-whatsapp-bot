"""Everything that talks to / about the WhatsApp Cloud API: signature check,
payload parsing, and sending messages."""
import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

import config

log = logging.getLogger(__name__)

GRAPH_URL = f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"


@dataclass
class IncomingMessage:
    message_id: str               # "wamid.XXXX" - unique, used for de-duplication
    wa_id: str                    # sender's number in international format, no '+': "919876543210"
    profile_name: Optional[str]   # the name shown in their WhatsApp profile (may be a nickname)
    type: str                     # "text", "image", "audio", "reaction", ...
    text: str                     # message body if type == "text", else ""


def verify_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Meta signs every POST with HMAC-SHA256(app_secret, body) in X-Hub-Signature-256.

    Without this check, anyone who finds your URL could POST fake messages and burn your
    OpenAI credits. If APP_SECRET isn't set we skip the check (fine for local dev only).
    """
    if not config.APP_SECRET:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(config.APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def parse_incoming_messages(payload: dict) -> list[IncomingMessage]:
    """Flatten Meta's nested webhook JSON into a simple list of messages.

    A text message arrives like this (trimmed):

        {
          "object": "whatsapp_business_account",
          "entry": [{
            "id": "<WABA_ID>",
            "changes": [{
              "field": "messages",
              "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "...", "phone_number_id": "..."},
                "contacts": [{"profile": {"name": "Rohit"}, "wa_id": "919876543210"}],
                "messages": [{
                  "from": "919876543210",
                  "id": "wamid.HBgL...",
                  "timestamp": "1712345678",
                  "type": "text",
                  "text": {"body": "kal haircut milega?"}
                }]
              }
            }]
          }]
        }

    Delivery receipts ("sent", "delivered", "read") arrive in the same envelope but have
    value["statuses"] instead of value["messages"], so we simply skip them.
    One POST can batch several entries, changes and messages, hence the nested loops.
    """
    found: list[IncomingMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})

            # wa_id -> profile name, so we can attach a name to each message
            names = {
                c.get("wa_id"): c.get("profile", {}).get("name")
                for c in value.get("contacts", [])
            }

            for m in value.get("messages", []):  # absent for status-only payloads
                msg_type = m.get("type", "unknown")
                found.append(
                    IncomingMessage(
                        message_id=m.get("id", ""),
                        wa_id=m.get("from", ""),
                        profile_name=names.get(m.get("from")),
                        type=msg_type,
                        text=m.get("text", {}).get("body", "") if msg_type == "text" else "",
                    )
                )
    return [m for m in found if m.message_id and m.wa_id]


def _post(body: dict) -> None:
    resp = httpx.post(
        GRAPH_URL,
        headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"},
        json=body,
        timeout=15,
    )
    if resp.status_code >= 400:
        # Meta's error body says exactly what's wrong (expired token, number not in allow-list...)
        log.error("WhatsApp API error %s: %s", resp.status_code, resp.text)
    resp.raise_for_status()


def send_text(to: str, text: str) -> None:
    _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    })


def mark_as_read(message_id: str) -> None:
    """Shows the blue ticks. Purely cosmetic, so failures are swallowed."""
    try:
        _post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except Exception:  # noqa: BLE001
        log.warning("Could not mark %s as read", message_id)
