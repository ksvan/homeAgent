# Mac Mini Production Deployment

This runbook describes running HomeAgent on a Mac mini.

**Current deployment method: git-sync (`scripts/auto-update.sh`).** The
Mac mini pulls code from GitHub itself and rebuilds — see "Pull-Based
Auto-Update" below. That's the section to follow for "how do I ship a
change." Every instruction in this doc assumes that method unless it
says otherwise.

The rest of this file (`scripts/prod.sh`, SSH keys, `rsync`) documents an
older push-from-this-Mac method and is kept only as a reference/fallback
— not the current workflow. Skip straight to "Pull-Based Auto-Update" if
you're setting this up for the first time.

## Prerequisites (legacy push-from-source-Mac method)

On the Mac mini:

1. Enable **Remote Login** in macOS System Settings.
2. Install OrbStack and start it once for the target user.
3. Verify SSH from this Mac:

```bash
ssh <user>@<mac-mini-hostname-or-ip> 'echo ok'
```

Use a stable LAN address or local hostname for the Mac mini. Examples:

```bash
export HOMEAGENT_DEPLOY_HOST=macmini.local
export HOMEAGENT_DEPLOY_USER=example
```

If your SSH target is already complete, use:

```bash
export HOMEAGENT_DEPLOY_TARGET=example@macmini.local
```

Optional remote install path:

```bash
export HOMEAGENT_DEPLOY_PATH=/Users/kristian/homeAgent
```

## SSH Key Setup (legacy)

The deploy script defaults to this local key if it exists:

```bash
~/.ssh/id_ed25519_homeagent
```

This machine already has that key pair. To authorize it on the Mac mini, run this once from the source Mac:

```bash
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh install-key
```

That command uses password SSH once, appends `~/.ssh/id_ed25519_homeagent.pub` to the Mac mini user's `~/.ssh/authorized_keys`, fixes remote SSH file permissions, and then tests key-based login.

If you need to generate a new deploy key later:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_homeagent -C homeagent-deploy
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh install-key
```

To use a different key:

```bash
export HOMEAGENT_SSH_KEY=~/.ssh/my_other_key
```

Then verify the deployment script can find Docker over SSH:

```bash
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh bootstrap
```

`prod.sh` adds standard Docker/OrbStack CLI paths for non-interactive macOS SSH sessions:

- `/Applications/Docker.app/Contents/Resources/bin`
- `$HOME/.docker/bin`
- `$HOME/.orbstack/bin`
- `/usr/local/bin`
- `/opt/homebrew/bin`

If `docker compose version` works in a local terminal on the Mac mini but fails over SSH, this is usually a PATH difference between interactive and SSH shells. If `bootstrap` finds Docker but says it cannot connect to the Docker daemon, log in to the Mac mini desktop session, start OrbStack, and enable OrbStack's login-start option for that user.

OrbStack must be running for the same macOS user that receives the SSH connection. `prod.sh` does not start OrbStack itself; it only calls the Docker CLI over SSH. If Docker contexts are not already configured for that user, run this once in a local terminal on the Mac mini:

```bash
docker context ls
docker context use orbstack
docker compose version
```

After that, `bootstrap`, `deploy`, `migrate`, `status`, and `logs` should use OrbStack through the normal `docker compose` CLI.

## First-Time Migration (legacy)

The one-time migration copies the current app, `.env`, prompts, and `data/` directory to the Mac mini.

Run:

```bash
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh migrate
```

What it does:

1. Creates a local backup in `data/backups/pre-migration`.
2. Stops the local Docker Compose stack with `./start.sh stop`.
3. Creates/verifies the remote app directory.
4. Syncs the code, `.env`, prompts, and `data/`, excluding `data/backups/`.
5. Builds and starts the remote Docker Compose stack.
6. Checks `http://127.0.0.1:8080/health` on the Mac mini.

The local stack remains stopped after migration. Keep it stopped while the Mac mini is production, otherwise two instances may process Telegram webhooks, Homey events, schedules, and reminders.

For a visible rsync progress meter during first migration:

```bash
HOMEAGENT_RSYNC_OPTS="--info=progress2" HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh migrate
```

If the first data sync drops with `Timeout, server ... not responding`, rerun the
same command. The migration is idempotent and rsync keeps partial files for
resume. You can also make SSH keepalives more forgiving for a noisy Wi-Fi link:

```bash
HOMEAGENT_SSH_ALIVE_INTERVAL=120 \
HOMEAGENT_SSH_ALIVE_COUNT=20 \
HOMEAGENT_RSYNC_OPTS="--info=progress2" \
HOMEAGENT_DEPLOY_HOST=macmini.local \
./scripts/prod.sh migrate
```

For production migration, prefer both Macs on wired Ethernet or stable Wi-Fi and
make sure the Mac mini is not sleeping during the transfer.

## Normal Deploys (legacy)

After the Mac mini owns production data, deploy code only:

```bash
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh deploy
```

`deploy` syncs source files and prompts, excludes `.env` and `data/`, rebuilds containers, starts the stack, and checks status.

Do not use `migrate` for regular deploys. It overwrites production `data/` from the source Mac.

If production `.env` needs to be restored or intentionally updated from this Mac:

```bash
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh sync-env
```

## Common Operations (legacy)

```bash
# Show remote service state and health endpoint
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh status

# Print recent remote logs and exit
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh logs-tail

# Follow remote logs until Ctrl-C
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh logs

# Restart production after changing .env directly on the Mac mini
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh restart

# Stop production
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh down

# Run production backup script on the Mac mini
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh backup

# Pull the latest cloudflared image and recreate just that container
HOMEAGENT_DEPLOY_HOST=macmini.local ./scripts/prod.sh update-cloudflared
```

`logs` intentionally does not finish on its own because it runs `docker compose logs -f`. Use `logs-tail` when you want a finite log snapshot.

`deploy`, `migrate`, and `restart` only run `docker compose build` + `up -d`. `build` rebuilds services with a `build:` context (`homeagent`, `tools`, `prometheus-mcp`) but never pulls the `cloudflared` service, since it uses `image: cloudflare/cloudflared:latest` instead. Once that image has been pulled once on the Mac mini, it stays cached indefinitely — use `update-cloudflared` to refresh it.

## Webhooks and Tunnel Cutover

The production Mac mini should be the only machine receiving inbound webhooks.

Before or during cutover, verify:

- `CLOUDFLARE_TUNNEL_TOKEN` in the Mac mini's `.env` belongs to the tunnel/public hostname you want production to use.
- The Cloudflare tunnel public hostname routes to `http://homeagent:8080`.
- `TELEGRAM_WEBHOOK_URL` points to the production public URL.
- Homey Advanced Flows point to the Mac mini LAN IP if they use direct LAN webhook URLs.

After the Mac mini's stack starts, reset Telegram to the production webhook URL if needed:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/webhook/telegram", "secret_token": "<YOUR_WEBHOOK_SECRET>"}'
```

## Publishing Web Chat Outside Your Home Network (Phase 5)

"Publishing" means: making web chat reachable from the public internet,
not just from a device on your home Wi-Fi. Right now, nobody outside
your house can reach web chat at all — your home router doesn't forward
its port to the internet. Publishing adds one thing: a rule inside
Cloudflare that says "requests for `chat.yourdomain.com` should be
tunneled to the Mac mini." Until you add that rule, none of the other
steps below have any real-world exposure — they're just getting things
ready. This is the moment covered by
[docs/household-identity-and-access-design.md](household-identity-and-access-design.md);
read that if you want the full reasoning, this section is just the "what
do I actually click and type" version of it.

### Two terms this section uses

- **Origin** — the exact web address a browser is currently showing,
  down to the protocol and port: `scheme://host:port`. Two addresses
  count as *different* origins even if they look almost identical:
  `https://chat.example.com` and `http://chat.example.com` are different
  (`https` vs `http`); `https://chat.example.com` and
  `https://chat.example.com:9091` are different (different port). This
  matters here because passkeys are cryptographically locked to the
  origin they were created on — a browser will flatly refuse to complete
  a passkey login if the page's current address doesn't exactly match
  what the app was told to expect. `WEBAUTHN_ORIGINS` in `.env` is
  exactly that list of expected addresses.
- **Public Hostname** — Cloudflare's name for one routing rule: "this
  domain name, tunneled to this address inside your Docker network."
  Telegram already has one of these (pointing `yourdomain.com` at
  `http://homeagent:8080`, the webhook port) — you're adding a second
  one, pointing a *different* subdomain at web chat's port instead.

### Steps, in order

**1. Decide the address people will actually type.** Pick a subdomain
for web chat, e.g. `chat.example.com` (reusing the same base domain
Telegram's webhook already uses is simplest, since it's already added to
Cloudflare). You'll use this exact address in two more places below, so
settle on it first.

**2. Edit `.env` on the Mac mini itself.** With the git-sync deployment
method, `.env` is never touched by a code update — you edit it directly,
on the mini (SSH in, or use its own screen/keyboard):

```bash
ssh <user>@<mac-mini-hostname-or-ip>
cd ~/homeAgent   # or wherever the repo lives on the mini
nano .env        # or any editor
```

Set (or add) these three lines, using the address you picked in step 1:

```text
FEATURE_WEB_CHAT=true
WEBAUTHN_RP_ID=example.com
WEBAUTHN_ORIGINS=https://chat.example.com,http://192.168.1.50:9090
```

- `WEBAUTHN_RP_ID` is just the bare domain — no `https://`, no
  subdomain, no port. It has to be a domain you actually own/control.
- `WEBAUTHN_ORIGINS` is the full list from the bullet above: the public
  web chat address (`https://chat.example.com`) **and** whatever address
  you personally open the admin dashboard from day to day (a LAN IP like
  `http://192.168.1.50:9090`, or a VPN hostname if you use one). If a
  page's address isn't in this list, passkey login on that page will
  fail outright — this is the single most common mistake here.
- Multiple addresses are comma-separated, no spaces.

If you skip this and try to publish anyway: the app is built to refuse
to start rather than run half-configured. If `APP_ENV=production` and
`FEATURE_WEB_CHAT=true` but `WEBAUTHN_RP_ID`/`WEBAUTHN_ORIGINS` are still
their `localhost` placeholder values, `app/__main__.py` exits
immediately with an error naming exactly this, instead of starting up in
a state where passkeys would silently fail (or worse). That's
deliberate — see "BR-01" in `SECURITY_REVIEW.md` for why.

**3. Apply the `.env` change.** Editing `.env` alone does nothing until
the containers restart — `auto-update.sh` only restarts things when it
pulls *new code*, not when you hand-edit config. Restart it yourself,
on the mini:

```bash
docker compose up -d
```

**4. Set up the first admin passkey** — see "First Admin Onboarding"
below. Do this now, before the next step, so there's a real way back
into the admin dashboard once web chat stops being LAN-only. (Admin
itself is unaffected by any of this — it never gets a public address.)

**5. Add the Cloudflare Public Hostname.** In the Cloudflare dashboard
(Zero Trust → Networks → Tunnels → your tunnel → Public Hostname tab):
add a new one with the subdomain from step 1, pointing at
`http://homeagent:9091` (web chat's port, addressed by its Docker
Compose service name — same pattern as the existing Telegram entry,
which points at `http://homeagent:8080`). This is the one step that
actually makes web chat reachable from outside your home — everything
before it was just getting ready.

**6. Verify it actually works, from a real device.** From your phone
(on cellular data, not your home Wi-Fi, so you know it's really going
over the internet and not just resolving locally), open the address
from step 1 and complete a passkey registration or login end-to-end.
Nothing in this project's automated tests can do this step — WebAuthn
ceremonies require a real browser talking to a real authenticator
(Face ID, Touch ID, a security key, etc.), so this manual check is the
actual release gate, not optional follow-up.

### Why the old picker can't come back by accident

Earlier versions of web chat had a fallback "pick your name from a list,
no password" login screen. It's not present in the code anymore at all
(not just switched off) — it was deleted outright specifically so there
is no configuration mistake that could bring it back once web chat is
public. If you're deploying from a commit older than that removal, stop
and update first:

```bash
git log --oneline -- app/webchat/api.py
```

should show exactly one commit (its deletion) and no file on disk. If it
shows more than that, or the file still exists, your checkout predates
this fix — do not publish yet.

## Checking Security Headers After Publishing (Phase 3)

Once step 5 above is done and web chat has a real public address, do
this one-time check to confirm Cloudflare is actually delivering the
protections the app sends, and that people can't bypass Cloudflare and
hit the Mac mini directly.

**1. Check the headers on the public address**, from any computer (this
one is fine, or your phone):

```bash
curl -sI https://chat.example.com/
```

(swap in your real address). Look for these lines in the output —
they're set by the app itself
(`app/webchat/security_headers.py`), so seeing them here confirms
Cloudflare is passing them through untouched, not stripping anything:

```text
content-security-policy: default-src 'self'; script-src 'self'; ...
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
```

If any of these are missing, something between the app and your browser
is rewriting the response — check whether the Cloudflare Public Hostname
you added has any "Transform Rules" or similar under that tunnel that
might strip headers (most setups have none, so this is unlikely, but
worth a quick look if the check fails).

**2. Confirm the Mac mini itself isn't reachable directly from the
internet**, bypassing Cloudflare entirely. From a device *not* on your
home network (phone on cellular, or ask a friend elsewhere to try):

```bash
curl -m 5 http://<your-home-public-ip>:9091/
```

This should time out or fail to connect — not return a page. If it
does connect, your home router is forwarding port 9091 to the Mac mini
directly, which means web chat is reachable two ways: through
Cloudflare (protected by rate limits, and eventually Cloudflare Access
if you add it) and directly (protected only by the app itself). Fix by
checking your router's port-forwarding configuration and removing any
rule for 9091 (or 9090, or 8080 outside of what Cloudflare's tunnel
itself needs) — the tunnel doesn't need any inbound port forwarding at
all, it makes an outbound-only connection to Cloudflare, which is the
whole point of using it instead of exposing a port yourself.

Both checks are one-time — once confirmed, Cloudflare's own routing and
your router's own configuration don't change on their own.

## First Admin Onboarding

There's no button in the app to "make someone an admin" — it only
happens one specific way, described below. This hasn't changed with any
of the web chat/passkey work above; it's the same on a brand new
deployment as it's always been.

**1. Add their Telegram ID to `.env`, on the Mac mini.** Get the
person's numeric Telegram user ID (not their @username) — ask them to
message `@userinfobot` on Telegram, it replies with their ID. Then, on
the mini:

```bash
ssh <user>@<mac-mini-hostname-or-ip>
cd ~/homeAgent
nano .env
```

Add (or extend) this line — comma-separated if more than one admin:

```text
ADMIN_TELEGRAM_IDS=123456789
```

Apply it:

```bash
docker compose up -d
```

**2. Have that person message the Telegram bot once** — anything works,
even just `/help`. The very first message from a Telegram ID creates
their account automatically, and if that ID is in `ADMIN_TELEGRAM_IDS`,
the new account is marked admin right then. This is the *only* thing
that ever sets admin status — there's no later step that grants it.

**3. Give them a passkey, so they can use the admin dashboard without
typing a shared secret every time.** This part uses the "break-glass"
shared secret (`APP_SECRET_KEY` in `.env`) as a one-time bootstrap key —
after this, they'll use Face ID/Touch ID/a security key instead.

- Open `http://<mac-mini-lan-ip>:9090/?token=<APP_SECRET_KEY>` in a
  browser on the same network as the mini (find the value of
  `APP_SECRET_KEY` in `.env`). The `?token=...` part logs you in once;
  the page removes it from the address bar automatically so it's never
  saved in browser history.
- In the dashboard, go to the **Access** tab, find that person's row,
  and click **Web chat invite**. This gives you a one-time link
  (`.../invite/<long-random-token>`).
- Send that link to the admin, and have *them* open it on *their own*
  device (phone, laptop — whichever they'll actually use). It walks
  them through creating a passkey. Once done, they can go straight to
  `http://<mac-mini-lan-ip>:9090/` and sign in with that passkey instead
  of any shared secret — the same passkey also works for the public web
  chat address once that's live (Cloudflare cutover section above),
  since it's tied to *them*, not to which page they registered it on.

**4. Have them register a second passkey on a second device too**, if
practical (e.g. phone *and* laptop). One lost phone becoming a full
admin lockout is exactly the scenario the break-glass secret below
exists to recover from — better to just not need it.

**5. Keep the break-glass secret working — don't try to "turn it off."**
`APP_SECRET_KEY` stays valid on every admin route forever, on purpose —
it's the documented recovery path if every passkey is ever lost, not a
temporary bootstrap step. Every time it's actually used for something
that changes data, it's logged (`admin.break_glass_used` in the audit
log) so its use is visible, not silent. Only reason to change it:
rotate it if you ever suspect it leaked —

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

paste the result into `.env` as the new `APP_SECRET_KEY`, then
`docker compose up -d` again on the mini.

## Pull-Based Auto-Update

**This is the current deployment method.** The Mac mini updates itself
by polling GitHub and pulling only once CI has passed on `main` — you
push/merge to `main` as normal, and the mini picks it up on its own
within 15 minutes (or immediately with a manual run). Nothing needs to
be run from this Mac to ship a code change.

### Setup

1. Add a `GITHUB_TOKEN` to the Mac mini's `.env` file — a GitHub fine-grained PAT with **read** access on **Actions** and **Contents** for this repo:

```text
GITHUB_TOKEN=github_pat_...
```

1. Create the logs directory if it does not exist:

```bash
mkdir -p ~/homeAgent/logs
```

1. Run a dry run to verify everything is wired up:

```bash
APP_DIR=$HOME/homeAgent $HOME/homeAgent/scripts/auto-update.sh --dry-run
```

### Manual run

```bash
$HOME/homeAgent/scripts/auto-update.sh
```

The script exits quietly if already up to date or if CI has not yet passed for the latest commit.

### Cron (simplest)

```bash
crontab -e
```

Add:

```text
*/15 * * * * $HOME/homeAgent/scripts/auto-update.sh >> $HOME/homeAgent/logs/auto-update.log 2>&1
```

### launchd (macOS-native alternative)

A template is provided at `scripts/auto-update.plist.example`. Copy it, replace `<YOUR_USERNAME>`, and load it with `launchctl`. See the comments inside the file for full instructions.

### What the script does

1. Fetches the latest SHA on `main` from the GitHub API.
2. Checks that the `lint-and-test` CI job completed with `success`.
3. Skips if local HEAD already matches, or if CI is pending/failed.
4. Runs `git pull --ff-only` then `docker compose up -d --build`.
5. Logs all steps with timestamps to `logs/auto-update.log`.

Local config (`.env`), data (`data/`), and prompts (`prompts/`) are never touched.

## Common Operations (current method)

All of these are run directly on the Mac mini (SSH in, or use its own
screen/keyboard) from the app directory — no `prod.sh`, no
`HOMEAGENT_DEPLOY_HOST`:

```bash
ssh <user>@<mac-mini-hostname-or-ip>
cd ~/homeAgent

docker compose ps                 # what's running, and its health status
docker compose logs -f            # follow logs (Ctrl-C to stop)
docker compose logs --tail=200    # last 200 lines, then exit
curl -fsS http://127.0.0.1:8080/health   # quick health check

docker compose up -d              # apply a .env change (no rebuild needed)
docker compose up -d --build      # apply a code change (same as auto-update.sh does)
docker compose down               # stop everything
```

For a database backup, see `scripts/backup.sh` — run it directly on the
mini the same way (`./scripts/backup.sh`).

## Consistency Notes

**Current method (git-sync):** `data/` never moves between machines at
all — it's not part of the git repository, so a code update on the Mac
mini (`git pull` + rebuild) never touches it. The mini's `data/` is the
one and only copy; back it up on the mini itself (`./scripts/backup.sh`
there), not from this Mac.

**Legacy method (`prod.sh`) only:** the app uses SQLite WAL files and
other local runtime state under `data/`. A consistent copy requires the
writer to be stopped before copying, which is why that older flow was
structured this way:

- `migrate` stops the local stack before syncing `data/`.
- Regular `deploy` excludes `data/`.
- Backups should be run on the production Mac mini after migration.

If you ever do need to move data between machines by hand, stop
whichever side is currently running first, then copy `data/` while it's
offline — never copy from a `data/` directory a running container is
still writing to.
