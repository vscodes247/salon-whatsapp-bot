"""Loads settings from environment variables (and a local .env file)."""
import os

from dotenv import load_dotenv

load_dotenv()

# WhatsApp Cloud API
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")

# Gemini
# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")

# Salon details (these end up in the system prompt)
SALON_NAME = os.getenv("SALON_NAME", "Glow & Grace Salon")
SALON_CITY = os.getenv("SALON_CITY", "Pune")
SALON_ADDRESS = os.getenv("SALON_ADDRESS", "Shop 12, FC Road, Pune 411004")
SALON_PHONE = os.getenv("SALON_PHONE", "+91 98765 43210")

# Misc
DB_PATH = os.getenv("DB_PATH", "salon.db")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "20"))


def validate() -> None:
    """Fail fast at startup if a required secret is missing."""
    required = {
        "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
        "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP_PHONE_NUMBER_ID,
        "VERIFY_TOKEN": VERIFY_TOKEN,
        "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )