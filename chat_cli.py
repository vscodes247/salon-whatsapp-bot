"""Chat with the bot in your terminal: test the prompt and booking tools without WhatsApp.

    python chat_cli.py

Uses a separate database (cli_test.db) so test chats don't pollute your real logs.
"""
import os

os.environ["DB_PATH"] = "cli_test.db"  # must be set before importing config

import config  # noqa: E402
import db  # noqa: E402
import llm  # noqa: E402

if not config.OPENROUTER_API_KEY:
    raise SystemExit("Set OPENROUTER_API_KEY in your .env first.")

db.init_db()
print("Salon bot CLI. Type a message (Ctrl+C to quit).\n")
while True:
    try:
        text = input("You: ").strip()
    except (KeyboardInterrupt, EOFError):
        break
    if text:
        print("Bot:", llm.generate_reply("cli-user", text, "Test User"), "\n")
