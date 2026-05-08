"""
Phase 0 spike: verify AgentMail connectivity and document payload shape.

Usage:
    uv run python scripts/agentmail_spike.py

Requires in .env:
    AGENTMAIL_API_KEY=...
    AGENTMAIL_INBOX_ID=...

What this checks:
- API key works
- Inbox is accessible
- Recent message field names and auth metadata shape
- Webhook subscriptions registered on the inbox
- Whether unauthenticated messages appear (message.received.unauthenticated support)
"""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("AGENTMAIL_API_KEY")
INBOX_ID = os.getenv("AGENTMAIL_INBOX_ID")

if not API_KEY or not INBOX_ID:
    sys.exit("Set AGENTMAIL_API_KEY and AGENTMAIL_INBOX_ID in .env before running")


def _pp(label: str, obj: object) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(json.dumps(obj if isinstance(obj, (dict, list)) else vars(obj) if hasattr(obj, "__dict__") else str(obj), indent=2, default=str))


def main() -> None:
    from agentmail import AgentMail

    client = AgentMail(api_key=API_KEY)

    # 1. List inboxes to verify API key
    print("\n[1] Listing inboxes to verify API key...")
    try:
        inboxes = client.inboxes.list()
        inbox_list = getattr(inboxes, "inboxes", inboxes)
        print(f"    OK — {len(inbox_list)} inbox(es) found")
        for inbox in inbox_list:
            print(f"    id={getattr(inbox, 'inbox_id', '?')}  address={getattr(inbox, 'address', '?')}")
    except Exception as e:
        sys.exit(f"    FAILED: {e}")

    # 2. List recent messages
    print(f"\n[2] Listing recent messages in inbox {INBOX_ID}...")
    try:
        messages = client.inboxes.messages.list(inbox_id=INBOX_ID, limit=5)
        msg_list = getattr(messages, "messages", messages)
        print(f"    OK — {len(msg_list)} message(s)")
    except Exception as e:
        sys.exit(f"    FAILED: {e}")

    if not msg_list:
        print("\n    No messages found. Send a test email to the inbox and re-run.")
        print("    (The spike will show the full payload shape once a message exists.)")
    else:
        # 3. Fetch full first message and dump all fields
        first = msg_list[0]
        msg_id = getattr(first, "message_id", None)
        print(f"\n[3] Fetching full message {msg_id}...")
        try:
            full = client.inboxes.messages.get(inbox_id=INBOX_ID, message_id=msg_id)
            _pp("Full message — all fields", full)

            # Show which top-level fields are present
            print("\n[4] Field inventory:")
            d = full.__dict__ if hasattr(full, "__dict__") else {}
            for k, v in d.items():
                vtype = type(v).__name__
                preview = repr(v)[:80] if v is not None else "None"
                print(f"    {k:<30} {vtype:<15} {preview}")

        except Exception as e:
            print(f"    FAILED to fetch full message: {e}")

    # 4. List webhook subscriptions
    print(f"\n[5] Listing webhook subscriptions...")
    try:
        webhooks = client.webhooks.list()
        wh_list = getattr(webhooks, "webhooks", webhooks) if not isinstance(webhooks, list) else webhooks
        if not wh_list:
            print("    No webhooks registered yet.")
        else:
            for wh in wh_list:
                print(f"    id={getattr(wh, 'webhook_id', '?')}  url={getattr(wh, 'url', '?')}  events={getattr(wh, 'events', '?')}")
    except Exception as e:
        print(f"    Could not list webhooks: {e}")

    # 5. Summarise findings
    print("""
[Summary]
- If messages listed and fetched: API key and inbox_id are working.
- Check field names above against the EmailMessage model in the design doc.
- Look for any spf/dkim/dmarc fields. If absent, AgentMail handles auth
  upstream (drops explicit failures before delivery).
- Check whether 'message.received.unauthenticated' appears in webhook event lists.
- Note whether text/html are present in webhook payloads vs only in full fetch.
""")


if __name__ == "__main__":
    main()
