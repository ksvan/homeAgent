# Mac Mini Production Deployment

This runbook describes the lightweight production path for running HomeAgent on a Mac mini while deploying from this development Mac over SSH.

The deployment model is:

- Source Mac: the machine with this working tree.
- Production Mac mini: runs Docker Compose through OrbStack and owns the active `.env`, `data/`, and webhooks.
- Transport: SSH plus `rsync`.
- Runtime: the existing `docker-compose.yml`.

## Prerequisites

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

## SSH Key Setup

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

## First-Time Migration

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

## Normal Deploys

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

## Common Operations

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
```

`logs` intentionally does not finish on its own because it runs `docker compose logs -f`. Use `logs-tail` when you want a finite log snapshot.

## Webhooks and Tunnel Cutover

The production Mac mini should be the only machine receiving inbound webhooks.

Before or during cutover, verify:

- `CLOUDFLARE_TUNNEL_TOKEN` in remote `.env` belongs to the tunnel/public hostname you want production to use.
- The Cloudflare tunnel public hostname routes to `http://homeagent:8080`.
- `TELEGRAM_WEBHOOK_URL` points to the production public URL.
- Homey Advanced Flows point to the Mac mini LAN IP if they use direct LAN webhook URLs.

After the remote stack starts, reset Telegram to the production webhook URL if needed:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/webhook/telegram", "secret_token": "<YOUR_WEBHOOK_SECRET>"}'
```

## Pull-Based Auto-Update

As an alternative to push deploys from the source Mac, the Mac mini can update itself by polling GitHub and pulling only when CI has passed.

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

## Consistency Notes

The app uses SQLite WAL files and other local runtime state under `data/`. A consistent migration requires the writer to be stopped before copying.

For that reason:

- `migrate` stops the local stack before syncing `data/`.
- Regular `deploy` excludes `data/`.
- Backups should be run on the production Mac mini after migration.

If you need to move production data back to this Mac later, stop the production stack first, then `rsync` production `data/` back while it is offline.
