# VPS Setup and Verification (Phase 1 / Checkpoint M1)

Installs the ClipForge Bot API on the Ubuntu VPS and verifies the VPS-side items of
Checkpoint M1 in `tasks/todo.md` (T07). Run every command on the VPS.

| Path | Purpose |
|---|---|
| `deploy/install.sh` | Idempotent provisioning and update script (run as root) |
| `deploy/systemd/clipforge-api.service` | API unit, installed to `/etc/systemd/system/` |
| `deploy/backend.env.example` | Template for `/etc/clipforge-bot/backend.env` (runtime, `root:clipforge 0640`) |
| `deploy/migration.env.example` | Template for `/etc/clipforge-bot/migration.env` (schema owner, `root:root 0600`) |

The migration credentials live in their own root-only file: only `install.sh` reads them, so
the API process never holds schema-changing rights (ADR-003).

## Prerequisites

- Ubuntu VPS with sudo, outbound HTTPS, at least 5 GB free disk (readiness checks the reserve).
- Supabase project `clipforge-bot`:
  - the database password of the `postgres` user (migrations);
  - the project ref and region of the **session pooler** host, port `5432` (ADR-004);
  - the CA certificate: *Project Settings → Database → SSL Configuration → Download certificate*.
- Telegram bot token from @BotFather and your numeric Telegram user ID.

## 1. Clone and first run

```bash
git clone https://github.com/abdtirtayasa24/clipforge-bot.git ~/clipforge-bot
cd ~/clipforge-bot
sudo ./deploy/install.sh
```

The first run installs packages (ffmpeg, git, curl, rsync, build tools), uv 0.11.7, the
`clipforge` service account and the directories, then copies both env templates into
`/etc/clipforge-bot/` and stops with `fill in real values and re-run`. That stop is expected.

## 2. Supabase CA certificate

```bash
sudo install -o root -g clipforge -m 0640 ~/prod-ca-2021.crt /etc/clipforge-bot/supabase-ca.crt
```

(Use the file name you downloaded.)

## 3. Migration credentials

```bash
sudo nano /etc/clipforge-bot/migration.env
```

Replace `PROJECT_REF`, `REGION` and `CHANGE_ME` with real values. URL-encode special characters
in the password (`?` → `%3F`, `@` → `%40`, `#` → `%23`, `%` → `%25`, `&` → `%26`).
Keep `sslmode=verify-full&sslrootcert=/etc/clipforge-bot/supabase-ca.crt`; the app refuses remote
connections without verified TLS.

## 4. Runtime settings

```bash
openssl rand -hex 32          # value for CLIPFORGE_SESSION_SECRET
sudo nano /etc/clipforge-bot/backend.env
```

Set `CLIPFORGE_DATABASE_URL` (the `clipforge_app_prod` login created in step 6; choose its
password now), `CLIPFORGE_TELEGRAM_BOT_TOKEN` and `CLIPFORGE_SESSION_SECRET`. The script refuses to
continue while any `CHANGE_ME` remains.

## 5. Install and migrate

```bash
sudo ./deploy/install.sh
```

Syncs the backend to `/opt/clipforge-bot/backend`, creates the venv as `clipforge`, runs
`uv pip install .`, records `uv pip freeze` in `/opt/clipforge-bot/releases/`, applies migrations
001–005 and installs `clipforge-api.service`.

Check the migration state:

```bash
cd /opt/clipforge-bot/backend
sudo env CLIPFORGE_MIGRATION_DATABASE_URL="$(sudo sed -n 's/^CLIPFORGE_MIGRATION_DATABASE_URL=//p' /etc/clipforge-bot/migration.env)" \
  .venv/bin/alembic current
```

Expected: `0005 (head)`.

## 6. Runtime database login

Migration 004 created the `NOLOGIN` group role `clipforge_app`. In the Supabase SQL editor create
the login the API uses, with the password from step 4:

```sql
CREATE ROLE clipforge_app_prod LOGIN PASSWORD '<password from step 4>' IN ROLE clipforge_app;
```

Through the pooler this user connects as `clipforge_app_prod.<project_ref>`, as in the template.

## 7. Register the administrator

`systemd-run` reads `backend.env` exactly like the service does:

```bash
sudo systemd-run --pipe --wait --quiet --uid=clipforge \
  --property=EnvironmentFile=/etc/clipforge-bot/backend.env \
  --working-directory=/opt/clipforge-bot/backend \
  /opt/clipforge-bot/backend/.venv/bin/python -m clipforge.admin_cli add-admin \
  --telegram-id <your telegram id> --name "<your name>"
```

Expected: a JSON log line `"msg": "admin enabled"`. The command is idempotent;
`deactivate-admin --telegram-id <id>` revokes access.

## 8. Start and verify the API

```bash
sudo systemd-analyze verify /etc/systemd/system/clipforge-api.service
sudo systemctl enable --now clipforge-api
systemctl status clipforge-api --no-pager
curl -s 127.0.0.1:8000/api/v1/health/live
curl -s -w '\n%{http_code}\n' 127.0.0.1:8000/api/v1/health/ready
```

Expected:

- `systemd-analyze verify` prints nothing.
- live: `{"status":"ok"}`.
- ready: HTTP `200` with `"database":"ok","workspace":"ok","disk":"ok","ffmpeg":"ok"`.
  A `503` lists the failing check names in `failing`; see Troubleshooting.

Logs (one JSON object per line, secrets redacted):

```bash
journalctl -u clipforge-api -n 50 --no-pager
```

## 9. Re-run is a no-op

```bash
sudo ./deploy/install.sh
ls /opt/clipforge-bot/releases/
```

Expected: no `backend source updated`, `installed`, `installing backend` or `restarting` lines;
only `applying database migrations` (already at head) and `done`. No new file in `releases/`.

## 10. Telegram admin login

The Mini App arrives in Phase 6. Until then, test with real `initData`: open any Mini App of this
bot (BotFather → *Configure Mini App*), and in its web console copy `Telegram.WebApp.initData`.
Within one hour:

```bash
curl -s -X POST 127.0.0.1:8000/api/v1/auth/telegram \
  -H 'Content-Type: application/json' \
  -d '{"init_data": "<paste initData>"}'
```

| Case | Expected |
|---|---|
| Your account (admin) | `200` with `access_token`, `token_type: "bearer"`, `expires_at` (12 h) |
| Another Telegram account | `403` `forbidden` |
| initData older than 1 h, or edited | `401` `unauthorized` |
| More than 10 failures in 5 minutes | `429` `rate_limited` |

Don't paste initData or tokens into chats or tickets; they grant access until they expire.

## Updating

```bash
cd ~/clipforge-bot && git pull && sudo ./deploy/install.sh
```

The script syncs code, reinstalls, migrates and restarts the API only when something changed.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `failing: ["database"]` | Wrong runtime URL or password, unencoded special characters, CA file missing or unreadable by `clipforge`, or step 6 not done. `journalctl -u clipforge-api` shows the error. |
| `remote database connections require sslmode=verify-full` | Add `?sslmode=verify-full&sslrootcert=...` to the URL. |
| `failing: ["workspace"]` | `/var/lib/clipforge-bot/workspace` missing or not owned by `clipforge`; re-run `install.sh`. |
| `failing: ["disk"]` | Less free space than `app_settings.reserved_disk_bytes` (5 GB). |
| `failing: ["ffmpeg"]` | ffmpeg/ffprobe not on `PATH`; re-run `install.sh`. |
| `auth is not configured` (503) | `CLIPFORGE_TELEGRAM_BOT_TOKEN` or `CLIPFORGE_SESSION_SECRET` (≥ 32 chars) missing. |
| Migration fails with permission errors | `migration.env` must use the Supabase `postgres` user, not `clipforge_app_prod`. |

## After verification

Tick the VPS items of T07 and Checkpoint M1 in `tasks/todo.md`, or send the outputs of steps 5,
8, 9 and 10 for review.
