# Setup Guide

Total time: ~30 minutes, mostly clicking through Meta's dashboard. Everything here is free.
Meta rearranges its dashboard menus now and then; the *names* of things (API Setup, Configuration,
Phone number ID) stay stable, so search the page for those if a menu has moved.

## 0. Prerequisites
- Python 3.10+
- A personal Facebook account (needed to log in to Meta for Developers)
- A phone with WhatsApp (you will chat with the bot from it)
- An OpenAI API key (https://platform.openai.com/api-keys). gpt-4o-mini costs a fraction of a cent per chat.
- ngrok (https://ngrok.com/download), free account

## 1. Install the project
```bash
cd whatsapp-salon-bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

## 2. Test the bot brain first (no WhatsApp needed)
Put `OPENAI_API_KEY=...` in `.env`, then:
```bash
python chat_cli.py
```
Try: `kal shaam ko haircut milega?`, `मुझे फेशियल करवाना है`, `book hair spa saturday 5pm, naam Priya`.
Fix the prompt/tools here before touching Meta. It's much faster to iterate.

## 3. Get WhatsApp Cloud API credentials (free)
1. Go to https://developers.facebook.com and log in. Accept the developer terms and verify your phone if asked.
2. **My Apps -> Create App.** Choose the use case **"Connect with customers through WhatsApp"**
   (older UI: app type **Business**). Name it anything, e.g. `salon-bot`.
3. In the app dashboard open **WhatsApp -> API Setup**. Meta has already created a **test phone number** for you.
   If it asks you to attach a business portfolio, create a free one or pick the default.
4. On that page copy:
   - **Phone number ID** -> `WHATSAPP_PHONE_NUMBER_ID` in `.env` (a long number; *not* the phone number itself)
   - **Temporary access token** -> `WHATSAPP_TOKEN` (**expires in 24 hours**, see step 3b)
5. In the **"To"** dropdown choose *Manage phone number list*, add **your own WhatsApp number**
   (with country code, e.g. +91...), and enter the code WhatsApp sends you. The test number can only message
   numbers on this list (max 5). This is the #1 reason for "it doesn't reply".
6. **App Secret:** **App settings -> Basic -> App secret -> Show** -> `APP_SECRET` in `.env`.
   Lets the server reject fake webhook calls.
7. **VERIFY_TOKEN:** make up any random string (e.g. `salon-verify-8f3k29`) and put it in `.env`. You'll paste
   the same string into Meta in step 5.

### 3b. Permanent token (so the bot doesn't die after 24h)
1. https://business.facebook.com/settings -> **Users -> System users -> Add** (role: Admin).
2. **Add assets** -> Apps -> select your app -> Full control.
3. **Generate new token** -> select your app -> tick `whatsapp_business_messaging` and
   `whatsapp_business_management` -> Generate. Paste into `WHATSAPP_TOKEN`.

## 4. Run the server and expose it with ngrok
Terminal 1: the app
```bash
uvicorn main:app --reload --port 8000
```
Terminal 2: the tunnel (Meta can only call public HTTPS URLs; ngrok gives your laptop one)
```bash
ngrok config add-authtoken <your-token>     # once, from the ngrok dashboard
ngrok http 8000
```
Copy the `https://xxxx.ngrok-free.app` URL it prints. **Free ngrok URLs change every time you restart ngrok**,
so you'll need to redo step 5 each time.

Sanity check: open `https://xxxx.ngrok-free.app/` in a browser. You should see `{"status":"ok"}`.

## 5. Connect the webhook
1. Meta dashboard -> **WhatsApp -> Configuration -> Webhook -> Edit**.
2. **Callback URL:** `https://xxxx.ngrok-free.app/webhook` (don't forget `/webhook`)
3. **Verify token:** the exact `VERIFY_TOKEN` from `.env`.
4. Click **Verify and save**. In Terminal 1 you should see `Webhook verified by Meta`.
5. Still on that page, under **Webhook fields**, click **Subscribe** next to **`messages`**.
   Verifying alone is not enough; without this subscription Meta never sends you messages.

## 6. Test it
1. On your phone, open WhatsApp and message the **test number** shown on the API Setup page
   (save it as a contact; it appears as "Test Number").
2. Send `Hi` or `kal haircut milega?`. The bot should reply within a few seconds.
3. Watch Terminal 1 for logs. Open **http://127.0.0.1:4040** (ngrok's inspector) to see every request Meta
   sends and replay it, which is great for debugging.

Replies are free as long as the customer messaged you in the last 24 hours (the "service window"), which
is always true for this bot because it only ever answers.

## 7. Look at the data
```bash
sqlite3 salon.db "SELECT * FROM bookings;"
sqlite3 salon.db "SELECT created_at, role, content FROM messages ORDER BY id DESC LIMIT 20;"
```
(`sqlite3` CLI not installed? Use https://sqlitebrowser.org or `python -m sqlite3 salon.db`.)

## Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| "Verify and save" fails | Server or ngrok not running; ngrok URL changed; missing `/webhook`; `VERIFY_TOKEN` mismatch (check for spaces) |
| Verified, but no messages arrive | You didn't subscribe to the `messages` field (step 5.5). Check ngrok inspector: if there are no POSTs, Meta isn't sending |
| Log shows `WhatsApp API error 401` / error code 190 | Access token expired (temporary tokens last 24h). Use the permanent token |
| `error 131030` "recipient not in allowed list" | Add your number under *API Setup -> To -> Manage phone number list* |
| `403 Bad signature` in ngrok inspector | `APP_SECRET` wrong. Copy it again from App settings -> Basic, or leave it empty while debugging |
| Bot replies twice | Rare; the server is too slow to ack and Meta retried. Dedup (`processed_messages`) should already prevent it; check logs |
| Bot ignores voice notes/images | By design: it replies asking for text. See "Limitations" in the README |
| Replies in the wrong language | Test in `chat_cli.py` and tweak the LANGUAGE section of the prompt in `llm.py` |

## Going beyond the test number (not needed for a demo)
To use your own business number you must add it in WhatsApp Manager, complete Meta business verification, and use
pre-approved *message templates* for anything sent outside the 24-hour window. Host the app on a real server
(Render, Railway, Fly.io, a VPS) instead of ngrok.
