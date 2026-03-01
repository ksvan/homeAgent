# Telegram Integration

HomeAgent uses Telegram as its primary messaging channel. This guide covers creating the bot, configuring it, and how the integration works in production and development.

---

## How It Works

HomeAgent receives messages via a **webhook** (production) or **polling** (development). Telegram calls your server's HTTPS endpoint with each incoming update. FastAPI handles the update, validates the secret token, and routes it to the agent.

Multi-user: each Telegram user who registers via `/start` becomes a member of the household. The bot can be added to a group chat or used via direct messages.

---

## Step 1: Create a Bot via BotFather

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a display name (e.g. "Home Assistant") — this is what users see
4. Choose a username (must end in `bot`, e.g. `myhome_bot`) — this is the handle
5. BotFather will return a **token**: `123456789:ABC-defGhIJKlmNopQrsTUVwxyZ`

Save this token — it goes into `TELEGRAM_BOT_TOKEN` in `.env`.

### Recommended bot settings (via BotFather)

```
/setprivacy → Disable
(allows bot to read all messages in group chats)

/setjoingroups → Disable
(prevents the bot being added to random groups)

/setcommands →
start - Register with the household
me - View your profile
family - View household members
forget - Clear your personal memories
```

---

## Step 2: Configure .env

```env
TELEGRAM_BOT_TOKEN=123456789:ABC-defGhIJKlmNopQrsTUVwxyZ
TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhook/telegram
TELEGRAM_WEBHOOK_SECRET=some-random-string-you-generate
```

Generate a webhook secret:
```bash
openssl rand -hex 32
```

---

## Step 3: Expose Your Server (Production)

Telegram requires a **publicly accessible HTTPS URL** for webhooks. Options:

### Option A — Reverse proxy with a domain (recommended for permanent setup)
Use nginx or Caddy as a reverse proxy with a domain and Let's Encrypt cert. The agent runs on port 8080 internally, proxy forwards HTTPS → internal port.

Caddy example (automatic HTTPS):
```
your-domain.com {
    reverse_proxy localhost:8080
}
```

### Option B — Tailscale + Funnel (easy for home setup)
If you use Tailscale, you can expose a local port via Tailscale Funnel without a domain or cert:
```bash
tailscale funnel 8080
```
This gives you a `https://<machine>.ts.net` URL.

### Option C — ngrok (for testing only)
```bash
ngrok http 8080
```
Gives a temporary `https://*.ngrok.io` URL. Not stable for production.

---

## Step 4: Register the Webhook

Once your server is running and publicly accessible:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-domain.com/webhook/telegram",
    "secret_token": "<YOUR_WEBHOOK_SECRET>",
    "allowed_updates": ["message", "callback_query"]
  }'
```

Verify it worked:
```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo"
```

---

## Development Mode (Polling)

In development (`APP_ENV=development`), the bot uses **long polling** instead of webhooks. No public URL needed.

Set `TELEGRAM_WEBHOOK_URL=` (empty) in `.env`. The app will automatically switch to polling mode.

---

## Access Control — Telegram User ID Allowlist

Access is controlled entirely by two environment variables in `.env`:

```text
ALLOWED_TELEGRAM_IDS=111111111,222222222,333333333
ADMIN_TELEGRAM_IDS=111111111
```

Any message from a Telegram user ID **not in `ALLOWED_TELEGRAM_IDS`** is silently dropped. No response, no error, no acknowledgement — the bot appears to not exist to unknown users. This is the outer gate. There is no invite flow, no password, no `/start` challenge.

`ADMIN_TELEGRAM_IDS` must be a subset of `ALLOWED_TELEGRAM_IDS`. Admins can manage policies, view logs, and use privileged commands. At least one admin must be defined.

Telegram user IDs cannot be faked — they are set by Telegram's infrastructure, not by the user's client. The webhook secret token confirms the request came from Telegram's servers, not an external caller.

### How to find a Telegram user ID

Open Telegram, search for `@userinfobot`, send it any message. It replies immediately with your numeric user ID.

### Setup steps

1. Each family member messages `@userinfobot` and shares their numeric ID with you
2. Add all IDs to `ALLOWED_TELEGRAM_IDS` in `.env`
3. Add your own ID (and any co-admins) to `ADMIN_TELEGRAM_IDS`
4. Start the bot — access is live immediately

### Adding a new member

1. Get their Telegram user ID (via `@userinfobot`)
2. Add it to `ALLOWED_TELEGRAM_IDS` in `.env`
3. Reload the bot config (no restart required — config is re-read on a scheduled interval, or use `/reload` admin command)

### Removing a member

Remove their ID from `ALLOWED_TELEGRAM_IDS` and reload. Their existing conversation history and memories remain in the DB unless explicitly purged with an admin command.

### First-time profile setup (onboarding)

When an allowlisted user messages the bot for the first time (ID in allowlist but no user record in DB), the bot introduces itself and asks for a name to address them by. This is purely for personalisation — not a security gate.

---

## Message Flow (technical)

```text
Telegram → POST /webhook/telegram
    │
    ├── FastAPI validates X-Telegram-Bot-Api-Secret-Token header
    ├── Parse Update object (message or callback_query)
    ├── Extract sender's telegram_user_id
    ├── Check against ALLOWED_TELEGRAM_IDS — if not in list: drop silently, return 200
    ├── If callback_query: route to confirmation handler
    ├── If message: TelegramChannel.parse_incoming() → Message
    ├── Look up User record in DB by telegram_user_id
    ├── If no record: begin first-time onboarding (ask for name)
    └── Route to agent pipeline → send response
```

Response is sent via `sendMessage` API call to the `chat_id`.

---

## Group Chat Support

The bot can operate in a group chat where multiple family members are present. To trigger the agent in a group:
- Mention the bot: `@myhome_bot turn on the living room lights`
- Or reply directly to a bot message

In group chats, the agent identifies the sender from their Telegram user ID (must be a registered household member).

---

## Limitations

- **File handling**: The bot can receive photos and documents but does not process them by default (planned for future)
- **Voice messages**: Not supported by default (future: Whisper transcription)
- **Reactions**: Not used
- **Rate limits**: Telegram allows 30 messages/second to different users; 1 message/second to the same user — sufficient for home use

---

## Troubleshooting

**Bot not responding:**
1. Check `TELEGRAM_BOT_TOKEN` is correct
2. Verify webhook is registered: `getWebhookInfo`
3. Check `last_error_message` in webhook info
4. Verify server is reachable from the internet on HTTPS port 443

**"Unauthorized" error:**
Token is wrong or has been revoked. Re-generate via BotFather with `/revoke`.

**Webhook not receiving updates:**
Ensure `secret_token` in the `setWebhook` call matches `TELEGRAM_WEBHOOK_SECRET` in `.env`.

**Messages delayed:**
Polling mode has inherent latency (~1–2s). Switch to webhook mode for production.
