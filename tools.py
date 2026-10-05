"""The tools the LLM can call: check availability and book an appointment.

Flow: LLM emits a tool call -> llm.py calls run_tool() -> we validate, hit SQLite,
and return a small dict -> llm.py hands that dict back to the LLM, which phrases
the reply in the customer's language.

Rule of thumb: the LLM only supplies what the customer said. Anything we already
know (their WhatsApp number) or must enforce (hours, capacity, past dates) is
handled here in Python, because the LLM can't be trusted with it.
"""
from datetime import date as Date
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import db

IST = ZoneInfo(config.TIMEZONE)  # name kept short; it's whatever TIMEZONE is set to

# ---------- salon configuration ----------
SERVICES = {
    "mens_haircut":   {"name": "Men's Haircut",   "price": 300},
    "womens_haircut": {"name": "Women's Haircut", "price": 600},
    "beard_trim":     {"name": "Beard Trim",      "price": 150},
    "hair_spa":       {"name": "Hair Spa",        "price": 1200},
    "hair_color":     {"name": "Hair Color",      "price": 2000},
    "facial":         {"name": "Facial",          "price": 800},
    "cleanup":        {"name": "Face Clean-up",   "price": 500},
    "manicure":       {"name": "Manicure",        "price": 500},
    "pedicure":       {"name": "Pedicure",        "price": 700},
    "threading":      {"name": "Eyebrow Threading", "price": 50},
}

OPEN_HOUR, CLOSE_HOUR = 10, 20          # open 10:00-20:00, last slot starts 19:00
SLOT_CAPACITY = 2                       # chairs/stylists available at the same time
MIN_LEAD_MINUTES = 30                   # can't book a slot starting in < 30 min
MAX_DAYS_AHEAD = 30
ALL_SLOTS = [f"{h:02d}:00" for h in range(OPEN_HOUR, CLOSE_HOUR)]
# Simplification: every service occupies exactly one hourly slot, whatever its real duration.

# ---------- tool schemas (what the LLM sees) ----------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": (
                "Get the free appointment start times for one date. Always call this "
                "before suggesting times or when the customer asks about availability. "
                "Never guess availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date as YYYY-MM-DD, e.g. 2025-03-14. Resolve 'kal', 'tomorrow', "
                                       "'Saturday' etc. using today's date from the system prompt.",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book ONE appointment. Call only after the customer has explicitly agreed to a "
                "specific service, date and time and has given their name. The customer's phone "
                "number is added automatically; never ask for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer's name as they gave it."},
                    "service": {
                        "type": "string",
                        "enum": list(SERVICES.keys()),
                        "description": "Service key from the price list.",
                    },
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {
                        "type": "string",
                        "description": "Start time, 24-hour HH:MM, on the hour, e.g. 17:00",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional. Extra services for the same visit or special requests.",
                    },
                },
                "required": ["customer_name", "service", "date", "time"],
            },
        },
    },
]


# ---------- helpers ----------
def _now() -> datetime:
    return datetime.now(IST)


def _parse_date(value: str) -> tuple[Date | None, str | None]:
    """Returns (date, None) if bookable, else (None, error_code)."""
    try:
        d = datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None, "invalid_date_format_use_YYYY-MM-DD"
    today = _now().date()
    if d < today:
        return None, "date_is_in_the_past"
    if d > today + timedelta(days=MAX_DAYS_AHEAD):
        return None, f"too_far_ahead_max_{MAX_DAYS_AHEAD}_days"
    return d, None


def _free_slots(d: Date) -> list[str]:
    """Slots on date `d` that are in the future and not full."""
    counts = db.get_slot_counts(d.isoformat())
    earliest = _now() + timedelta(minutes=MIN_LEAD_MINUTES)
    free = []
    for slot in ALL_SLOTS:
        start = datetime.combine(d, datetime.strptime(slot, "%H:%M").time(), tzinfo=IST)
        if start >= earliest and counts.get(slot, 0) < SLOT_CAPACITY:
            free.append(slot)
    return free


# ---------- tool implementations ----------
def get_available_slots(date: str) -> dict:
    d, err = _parse_date(date)
    if err:
        return {"ok": False, "error": err}
    free = _free_slots(d)
    return {
        "ok": True,
        "date": d.isoformat(),
        "weekday": d.strftime("%A"),
        "available_slots": free,  # 24h HH:MM; the LLM converts to 12h for the customer
        "note": "" if free else "No slots left on this date.",
    }


def book_appointment(
    wa_id: str,
    customer_name: str = "",
    service: str = "",
    date: str = "",
    time: str = "",
    notes: str = "",
) -> dict:
    customer_name = (customer_name or "").strip()[:60]
    notes = (notes or "").strip()[:200]
    if not customer_name:
        return {"ok": False, "error": "customer_name_required"}
    if service not in SERVICES:
        return {"ok": False, "error": "unknown_service", "valid_services": list(SERVICES)}
    if time not in ALL_SLOTS:
        return {"ok": False, "error": "invalid_time_use_hourly_HH:MM", "valid_times": ALL_SLOTS}
    d, err = _parse_date(date)
    if err:
        return {"ok": False, "error": err}

    # Re-check "not in the past / too soon" here, not just in the slot listing.
    if time not in _free_slots(d):
        free = _free_slots(d)
        return {"ok": False, "error": "slot_unavailable", "available_slots": free}

    booking_id = db.create_booking(
        wa_id, customer_name, service, d.isoformat(), time, notes, SLOT_CAPACITY
    )
    if booking_id is None:  # someone grabbed it between the check above and the insert
        return {"ok": False, "error": "slot_unavailable", "available_slots": _free_slots(d)}

    return {
        "ok": True,
        "booking_id": booking_id,
        "customer_name": customer_name,
        "service": SERVICES[service]["name"],
        "price_inr": SERVICES[service]["price"],
        "date": d.isoformat(),
        "weekday": d.strftime("%A"),
        "time": time,
        "notes": notes,
    }


def run_tool(name: str, args: dict, wa_id: str) -> dict:
    """Dispatcher called by llm.py. Never raises: errors go back to the LLM as data."""
    try:
        if name == "get_available_slots":
            return get_available_slots(date=args.get("date", ""))
        if name == "book_appointment":
            return book_appointment(
                wa_id=wa_id,  # injected by us, never taken from the LLM
                customer_name=args.get("customer_name", ""),
                service=args.get("service", ""),
                date=args.get("date", ""),
                time=args.get("time", ""),
                notes=args.get("notes", ""),
            )
        return {"ok": False, "error": f"unknown_tool_{name}"}
    except Exception as exc:  # noqa: BLE001 - we want the LLM to apologise gracefully
        return {"ok": False, "error": f"internal_error: {type(exc).__name__}"}
