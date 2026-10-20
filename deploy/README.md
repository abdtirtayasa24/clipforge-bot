# VPS Setup and Verification

Installs ClipForge Bot on the Ubuntu VPS and verifies the VPS-side checkpoint items in
`tasks/todo.md`: steps 1–10 for Checkpoint M1, sections 11–12 for Checkpoint M2. Run every
command on the VPS.

| Path | Purpose |
|---|---|
| `deploy/install.sh` | Idempotent provisioning and update script (run as root) |
| `deploy/systemd/clipforge-api.service` | API unit, installed to `/etc/systemd/system/` |
| `deploy/systemd/clipforge-discovery.{service,timer}` | Daily YouTube discovery at 03:00 (oneshot + timer) |
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
- YouTube Data API v3 key (Google Cloud console → *APIs & Services → Credentials*; restrict it to
  the YouTube Data API v3). Discovery uses about 10 of the 10,000 daily quota units per trending run.

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
password now), `CLIPFORGE_TELEGRAM_BOT_TOKEN`, `CLIPFORGE_SESSION_SECRET`, `YOUTUBE_API_KEY`, `GEMINI_API_KEY` and `OPENROUTER_API_KEY`. The script refuses to
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
sudo systemctl enable --now clipforge-api clipforge-discovery.timer
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

## 11. Discovery (Checkpoint M2)

Servers installed before Phase 2 need the YouTube key and the timer once:

```bash
cd ~/clipforge-bot && git pull
echo 'YOUTUBE_API_KEY=<your key>' | sudo tee -a /etc/clipforge-bot/backend.env >/dev/null
sudo ./deploy/install.sh
sudo systemctl enable --now clipforge-discovery.timer
sudo systemd-analyze verify /etc/systemd/system/clipforge-discovery.{service,timer}
systemctl list-timers clipforge-discovery.timer
```

Expected: `verify` prints nothing; `list-timers` shows the next run at 03:00 (VPS time zone).

Add discovery sources with a session token from step 10 (`TOKEN=<access_token>`):

```bash
API=127.0.0.1:8000/api/v1
curl -s -X POST $API/sources -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json'   -d '{"kind": "trending", "name": "Trending Indonesia", "region_code": "ID"}'
# Optional monitored channel (24-character ID starting with UC):
curl -s -X POST $API/sources -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json'   -d '{"kind": "channel", "name": "<channel name>", "channel_id": "UC..."}'
curl -s $API/sources -H "Authorization: Bearer $TOKEN"
```

Run discovery by hand, through the timer's unit and through the API:

```bash
sudo systemctl start clipforge-discovery.service
systemctl status clipforge-discovery.service --no-pager
journalctl -u clipforge-discovery -n 20 --no-pager
curl -s -w '
%{http_code}
' -X POST $API/discovery/run -H "Authorization: Bearer $TOKEN"
curl -s "$API/videos?limit=5" -H "Authorization: Bearer $TOKEN"
```

Expected:

- The service ends `inactive (dead)` with `status=0/SUCCESS`; the journal shows
  `"msg": "discovery finished"` with `quota_units` (about 10 per trending source, 3 per channel),
  `new_videos`, `updated_videos` and `failed_sources: []`.
- The API answers `202 {"status":"started"}`. A second `POST` while a run is still going answers
  `409` `discovery is already running`; a timer run that overlaps logs `already running; skipped`.
- Running discovery again creates no duplicate videos: `new_videos` counts only videos not seen
  before, and `updated_videos` the ones refreshed.
- `/videos` lists only 30–120 minute videos, each with `"rights_status": "unverified"` and
  `"source_status": "awaiting_source"`. The trending chart is mostly short videos, so few or no new
  videos per run is normal; monitored channels are the more reliable long-form source.

The day after, `journalctl -u clipforge-discovery --since yesterday` shows the 03:00 run.

## 12. Authorized ingestion (Checkpoint M2)

Needs `TOKEN` and `API` from section 11 and a discovered video (`VIDEO=<id from /videos>`). Only
use media you are authorized to process.

### Uploaded file

Copy a 30–120 minute authorized file to the VPS (for example `scp talk.mp4 vps:~/`), then upload it
in 8 MiB chunks:

```bash
FILE=~/talk.mp4
SIZE=$(stat -c %s "$FILE"); SHA=$(sha256sum "$FILE" | cut -d' ' -f1)
UPLOAD=$(curl -s -X POST $API/uploads -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"video_id\": \"$VIDEO\", \"filename\": \"talk.mp4\", \"size_bytes\": $SIZE, \"sha256\": \"$SHA\",
       \"permission\": {\"type\": \"own_content\", \"statement\": \"I am the creator of this video.\"}}" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
CHUNK=$((8 * 1024 * 1024)); OFFSET=0
while [ "$OFFSET" -lt "$SIZE" ]; do
  END=$(( OFFSET + CHUNK < SIZE ? OFFSET + CHUNK - 1 : SIZE - 1 ))
  dd if="$FILE" bs=1M skip=$((OFFSET / 1048576)) count=8 status=none \
    | curl -s -o /dev/null -w '%{http_code} ' -X PATCH $API/uploads/$UPLOAD \
        -H "Authorization: Bearer $TOKEN" -H "Content-Range: bytes $OFFSET-$END/$SIZE" \
        -H 'Content-Type: application/octet-stream' --data-binary @-
  OFFSET=$((END + 1))
done
curl -s -X POST $API/uploads/$UPLOAD/complete -H "Authorization: Bearer $TOKEN"
```

Expected: every chunk `200`; `complete` returns `"state": "ready"`. If a chunk fails, `GET
$API/uploads/$UPLOAD` shows `received_bytes`; resume from that offset. A wrong checksum or invalid
media returns `422` and removes the staged file.

### Permitted URL

There is no settings API before Phase 5; allow the media host once in the Supabase SQL editor
(exact host, or `*.example.com` for its subdomains):

```sql
UPDATE app_settings SET value = '["media.example.com"]' WHERE key = 'permitted_download_domains';
```

```bash
VIDEO2=<another video id>
curl -s -X POST $API/videos/$VIDEO2/media -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://media.example.com/talk.mp4",
       "permission": {"type": "creator_permission", "statement": "<who allowed it and how>"}}'
# Refused (422 url_rejected): YouTube, http://, private addresses, hosts not on the list
curl -s -X POST $API/videos/$VIDEO2/media -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.youtube.com/watch?v=x", "permission": {"type": "own_content", "statement": "test refusal"}}'
```

### Run the jobs

```bash
for V in $VIDEO $VIDEO2; do
  curl -s -X POST $API/videos/$V/jobs -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)"
  echo
done
# The worker unit arrives in Phase 4; until then run it as a transient unit.
sudo systemd-run --unit=clipforge-worker-m2 --uid=clipforge \
  --property=EnvironmentFile=/etc/clipforge-bot/backend.env \
  --working-directory=/opt/clipforge-bot/backend \
  /opt/clipforge-bot/backend/.venv/bin/python -m clipforge.workers.main
journalctl -u clipforge-worker-m2 -f      # Ctrl+C once both jobs finished
sudo systemctl stop clipforge-worker-m2   # SIGTERM: the worker exits cleanly
curl -s "$API/jobs?limit=2" -H "Authorization: Bearer $TOKEN"
curl -s $API/jobs/<job id> -H "Authorization: Bearer $TOKEN"
```

Expected: each job's events show `stage_changed` to `ingesting` and then `validated`. Until the
Phase 3 stages exist the pipeline ends there, so the jobs finish `completed`. The URL job's media
asset becomes `ready` and both videos turn `"rights_status": "authorized"`. Repeating a `POST
/videos/{id}/jobs` with the same `Idempotency-Key` returns the same job.

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
| `failing: ["ai_providers"]` | A selected model (`app_settings.models`) is missing or disabled in `ai_provider_configs`, or its key env var is unset; `journalctl -u clipforge-api` names the missing variable. Add it to `backend.env` and restart. |
| `YOUTUBE_API_KEY is not set` (discovery exits 2) / `YouTube API key is not configured` (503) | Add `YOUTUBE_API_KEY` to `backend.env` and restart `clipforge-api`. |
| `failed_sources` not empty / `quotaExceeded` | Check the key and its API restriction; the daily quota resets at midnight Pacific time. |
| `auth is not configured` (503) | `CLIPFORGE_TELEGRAM_BOT_TOKEN` or `CLIPFORGE_SESSION_SECRET` (≥ 32 chars) missing. |
| Migration fails with permission errors | `migration.env` must use the Supabase `postgres` user, not `clipforge_app_prod`. |

## After verification

Tick the VPS items in `tasks/todo.md` (T07 and Checkpoint M1 for steps 1–10, T11 and Checkpoint
M2 for sections 11–12), or send the outputs for review.
