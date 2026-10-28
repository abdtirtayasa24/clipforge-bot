# VPS Setup and Verification

Installs ClipForge Bot on the Ubuntu VPS and verifies the VPS-side checkpoint items in
`tasks/todo.md`: steps 1–10 for Checkpoint M1, sections 11–12 for Checkpoint M2. Run every
command on the VPS.

| Path | Purpose |
|---|---|
| `deploy/install.sh` | Idempotent provisioning and update script (run as root) |
| `deploy/systemd/clipforge-api.service` | API unit, installed to `/etc/systemd/system/` |
| `deploy/systemd/clipforge-discovery.{service,timer}` | Daily YouTube discovery at the hour set in Settings, default 03:00 (oneshot + hourly timer, ADR-035) |
| `deploy/backend.env.example` | Template for `/etc/clipforge-bot/backend.env` (runtime, `root:clipforge 0640`) |
| `deploy/migration.env.example` | Template for `/etc/clipforge-bot/migration.env` (schema owner, `root:root 0600`) |
| `deploy/nginx/clipforge-bot.conf` | Nginx site for `clipforge.thetirtayasa.my.id`: HTTPS, Mini App files, `/api/` proxy (section 15) |
| `deploy/deploy_frontend.sh` | Builds the Mini App and publishes it to `/var/www/clipforge-bot` (section 15) |

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

The first run installs packages (ffmpeg, the DejaVu subtitle font, git, curl, rsync, build tools), uv 0.11.7, the
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
`uv pip install .`, records `uv pip freeze` in `/opt/clipforge-bot/releases/`, applies every
migration and installs the systemd units.

Check the migration state:

```bash
cd /opt/clipforge-bot/backend
sudo env CLIPFORGE_MIGRATION_DATABASE_URL="$(sudo sed -n 's/^CLIPFORGE_MIGRATION_DATABASE_URL=//p' /etc/clipforge-bot/migration.env)" \
  .venv/bin/alembic current
```

Expected: the newest revision in `backend/migrations/versions/` followed by `(head)`, e.g. `0008 (head)`.

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

Expected: `verify` prints nothing; `list-timers` shows the next hourly tick. Discovery itself runs only
in the hour set in Mini App → Settings → YouTube discovery (default 03:00, VPS time zone).

Add discovery sources with a session token from step 10 (`TOKEN=<access_token>`):

```bash
API=127.0.0.1:8000/api/v1
curl -s -X POST $API/sources -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json'   -d '{"kind": "trending", "name": "Trending Indonesia", "region_code": "ID"}'
# Optional monitored channel (24-character ID starting with UC):
curl -s -X POST $API/sources -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json'   -d '{"kind": "channel", "name": "<channel name>", "channel_id": "UC..."}'
curl -s $API/sources -H "Authorization: Bearer $TOKEN"
```

Run discovery by hand, through the CLI and through the API (`systemctl start clipforge-discovery.service`
only runs it within the configured hour):

```bash
sudo systemd-run --wait --pipe --uid=clipforge --gid=clipforge \n  -p EnvironmentFile=/etc/clipforge-bot/backend.env -p WorkingDirectory=/opt/clipforge-bot/backend \n  /opt/clipforge-bot/backend/.venv/bin/python -m clipforge.discovery_cli
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

The day after, `journalctl -u clipforge-discovery --since yesterday` shows the run at the configured hour.

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

Expected: each job's events show `stage_changed` to `ingesting` and then `validated`. From
Phase 3 on the jobs continue into the AI stages, which need the keys from section 13. The URL job's media
asset becomes `ready` and both videos turn `"rights_status": "authorized"`. Repeating a `POST
/videos/{id}/jobs` with the same `Idempotency-Key` returns the same job.

## 13. AI analysis (Checkpoint M3)

Needs `TOKEN`, `API` and one 60–120 minute authorized video that reached `validated` in section
12 (or upload a new one the same way). On the Gemini free tier (and free OpenRouter models) the
audio, transcript and frames are used by the provider to improve its products (ADR-027); only
process media whose creator authorization allows that.

### Keys and quotas

Add the AI keys to `/etc/clipforge-bot/backend.env` (`GEMINI_API_KEY` from Google AI Studio;
`OPENROUTER_API_KEY` for the analysis fallback) and restart the API:

```bash
sudo nano /etc/clipforge-bot/backend.env
sudo systemctl restart clipforge-api
curl -s https://clipforge.thetirtayasa.my.id/api/v1/health/ready          # "ai_providers": "ok"
curl -s $API/ai/providers -H "Authorization: Bearer $TOKEN"  # selected models, prices, used_today
```

The seeded `rpm_limit` / `rpd_limit` values are placeholders (ADR-028). Replace them with the
limits AI Studio shows for your project, in the Supabase SQL editor:

```sql
UPDATE ai_provider_configs SET rpm_limit = <rpm>, rpd_limit = <rpd>
WHERE provider = 'gemini' AND model = 'gemini-3.5-transcribe';   -- repeat per model
```

### Run the job

```bash
curl -s -X POST $API/videos/$VIDEO/jobs -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)"
# The worker unit arrives in Phase 4; until then run it as a transient unit.
sudo systemd-run --unit=clipforge-worker-m3 --uid=clipforge \
  --property=EnvironmentFile=/etc/clipforge-bot/backend.env \
  --working-directory=/opt/clipforge-bot/backend \
  /opt/clipforge-bot/backend/.venv/bin/python -m clipforge.workers.main
journalctl -u clipforge-worker-m3 -f      # Ctrl+C once the job finished
sudo systemctl stop clipforge-worker-m3
curl -s $API/jobs/<job id> -H "Authorization: Bearer $TOKEN"
```

Expected: `stage_changed` events for `audio_extracted`, `transcribed`, `analyzed` and
`candidates_selected`, then `completed` (rendering arrives in Phase 4), or `completed_no_clips`
when no candidate scores at least `min_score`. A `quota_deferred` event means a daily quota ran
out: the job waits for the reset without using an attempt. `costs` shows the estimated total.

### Review the candidates

There is no clips API before Phase 5; inspect the results in the Supabase SQL editor:

```sql
-- Candidates: selected ones first (≤ 3, each 60–90 s by default, total ≥ 75)
SELECT rank, selected, total_score, round((end_ms - start_ms) / 1000.0, 1) AS seconds,
       to_char(make_interval(secs => start_ms / 1000.0), 'HH24:MI:SS') AS starts_at,
       title, hook_line, hook_score, emotion_score, clarity_score, relevance_score,
       visual_score, completeness_score, explanation
FROM clip_candidates WHERE job_id = '<job id>'
ORDER BY selected DESC, rank, total_score DESC;

-- Provider calls: latency, tokens or audio seconds, estimated cost per stage
SELECT stage, provider, model, count(*) AS calls, count(*) FILTER (WHERE NOT success) AS failed,
       round(avg(latency_ms)) AS avg_ms, sum(audio_seconds) AS audio_s,
       sum(input_tokens) AS tokens_in, sum(output_tokens) AS tokens_out,
       sum(estimated_cost) AS cost
FROM provider_usage WHERE job_id = '<job id>' GROUP BY stage, provider, model ORDER BY stage;

-- Stage durations from the job timeline
SELECT stage, created_at, created_at - lag(created_at) OVER (ORDER BY id) AS took
FROM job_events WHERE job_id = '<job id>' AND event_type IN ('claimed', 'stage_changed')
ORDER BY id;
```

Watch the selected clips at their `starts_at` times in the source and judge whether they work as
standalone shorts (hook, completeness, accurate title). Note what should change in the prompts
or thresholds (`min_score`, `clip_min_s`, `clip_max_s`) before rendering work starts.

## 14. Rendering, Google Drive and notifications (Checkpoint M4)

Needs `TOKEN`, `API`, the AI keys from section 13 and one 60–120 minute authorized video that
reached `validated` (section 12).

### Google Drive OAuth client

In Google Cloud console (any project you own):

1. **APIs & Services → Library**: enable the **Google Drive API**.
2. **OAuth consent screen**: user type External, add your Google account as a test user, add the
   scope `https://www.googleapis.com/auth/drive.file`. Then **Publish app** (status "In
   production"): while the app is in "Testing", Google expires its refresh tokens after 7 days
   and Drive would need reconnecting every week. `drive.file` is a non-sensitive scope, so no
   verification is needed.
3. **Credentials → Create credentials → OAuth client ID**: type "Web application", authorized
   redirect URI `https://clipforge.thetirtayasa.my.id/api/v1/integrations/drive/callback`.

Add the four Drive settings to `/etc/clipforge-bot/backend.env` (see `backend.env.example`):

```bash
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # CLIPFORGE_TOKEN_ENCRYPTION_KEY
sudo nano /etc/clipforge-bot/backend.env
```

Back up `CLIPFORGE_TOKEN_ENCRYPTION_KEY` (for example in your password manager): without it the
stored refresh token cannot be decrypted and Drive has to be connected again.

### Install the worker unit

```bash
sudo systemctl stop clipforge-worker-m2 clipforge-worker-m3 2>/dev/null   # earlier transient workers
cd ~/clipforge-bot && git pull && sudo ./deploy/install.sh   # subtitle font + clipforge-worker.service
sudo systemctl restart clipforge-api
sudo systemctl enable --now clipforge-worker
systemctl status clipforge-worker          # active (running), Memory max 1.8G
journalctl -u clipforge-worker -n 20       # "worker started"
```

### Connect Drive

```bash
curl -s $API/integrations/drive/connect -H "Authorization: Bearer $TOKEN"   # {"auth_url": ...}
```

Open the `auth_url` in a browser signed in to the Google account that should own the clips,
allow access, and wait for "Google Drive is connected". The link works once and for 10 minutes;
call `connect` again for a new one. Then:

```bash
curl -s $API/integrations/drive/status -H "Authorization: Bearer $TOKEN"
# "status": "connected", "scopes": [".../drive.file"], "account_email": ..., no token values
```

The app creates its own folder **ClipForge Bot** on the first upload (`target_folder_id` is
filled in after that); with the `drive.file` scope it cannot see any other Drive files.

### Run the job and record resources

Start a resource log in a second terminal before queueing the job:

```bash
while sleep 10; do
  printf '%s mem=%s peak=%s free=%s\n' "$(date +%T)" \
    "$(systemctl show clipforge-worker -p MemoryCurrent --value)" \
    "$(systemctl show clipforge-worker -p MemoryPeak --value)" \
    "$(df -B1 --output=avail /var/lib/clipforge-bot | tail -1)"
done | tee ~/m4-resources.log
```

```bash
curl -s -X POST $API/videos/$VIDEO/jobs -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)"
journalctl -u clipforge-worker -f          # "clip rendered", "clip uploaded"
curl -s $API/jobs/<job id> -H "Authorization: Bearer $TOKEN"
```

Expected: after `candidates_selected` the stages `rendering_clips`, `rendering`, `uploading_clips`
and `uploading`, then `completed`, and a Telegram message "N clips ready for review" listing the
titles, scores and estimated AI cost. In Drive, the **ClipForge Bot** folder holds one private MP4
per clip, named after its title plus a short clip id. Check the files in the Supabase SQL editor:

```sql
SELECT c.title, c.drive_file_id, a.upload_state, a.size_bytes, a.md5, a.local_deleted_at,
       round((c.end_ms - c.start_ms) / 1000.0, 1) AS seconds
FROM clips c JOIN clip_artifacts a ON a.clip_id = c.id AND a.kind = 'video'
WHERE c.job_id = '<job id>';
```

Every row should be `verified` with a Drive id and `local_deleted_at` set. Download one clip and
confirm 720×1280, burned subtitles and sound (`ffprobe -v error -show_streams clip.mp4`). Stage
timings come from the `job_events` query in section 13. From `~/m4-resources.log`, the largest
`peak` must stay below 1.8 GB (1932735283 bytes) and the smallest `free` at or above 5 GB
(5368709120 bytes).

### Kill the worker during the upload

Queue a second job (another video, or the same one after the first completed). While it is in
`uploading`, which this query shows as `uploading` rows:

```sql
SELECT a.upload_state, count(*) FROM clip_artifacts a JOIN clips c ON c.id = a.clip_id
WHERE c.job_id = '<job id>' GROUP BY 1;
```

kill the worker without a clean shutdown:

```bash
sudo systemctl kill -s SIGKILL clipforge-worker
journalctl -u clipforge-worker -f   # restarted after 10 s; the job is reclaimed once its
                                    # 5-minute lease expires, then "clip uploaded"
```

Expected: the job completes and each clip has exactly one Drive file. Confirm there is no
duplicate:

```sql
SELECT c.id, c.drive_file_id, a.upload_state FROM clips c
JOIN clip_artifacts a ON a.clip_id = c.id AND a.kind = 'video' WHERE c.job_id = '<job id>';
```

The **ClipForge Bot** folder must show one file per clip id (the file names end with the first 8
characters of the clip id), with nothing extra in Drive's trash other than copies the worker
replaced after a checksum mismatch.

### Notifications

Failures and a disconnected Drive also notify the admin chat. Turn notifications off or on in
the Mini App: *Settings → Notifications* (section 15).

## 15. Telegram Mini App (Checkpoint M5)

### Domain and SSL certificate

The Mini App is served from its own Nginx site, `clipforge.thetirtayasa.my.id`, so the other
sites and their TLS settings on this Nginx stay untouched. Telegram only opens Mini Apps over
HTTPS, so get the Let's Encrypt certificate first.

1. **DNS:** add an `A` record `clipforge` → the VPS public IPv4 (and an `AAAA` record if the VPS
   has IPv6) at the DNS provider of `thetirtayasa.my.id`. Wait until it resolves:

   ```bash
   dig +short clipforge.thetirtayasa.my.id      # the VPS IP
   ```

2. **Firewall:** ports 80 and 443 open (`sudo ufw allow 'Nginx Full'` if ufw is active). Port 80
   stays open: Let's Encrypt validates and renews over HTTP.

3. **Certificate:** install certbot with its Nginx plugin and request the certificate. `certonly`
   only obtains the certificate; certbot does not edit any Nginx file.

   ```bash
   sudo apt-get install -y certbot python3-certbot-nginx
   sudo certbot certonly --nginx -d clipforge.thetirtayasa.my.id \
     --agree-tos -m <your-email> --no-eff-email
   sudo ls /etc/letsencrypt/live/clipforge.thetirtayasa.my.id/   # fullchain.pem privkey.pem ...
   ```

4. **Renewal:** certbot's systemd timer renews automatically about 30 days before expiry. Reload
   Nginx after each renewal so it serves the new certificate, then test a renewal:

   ```bash
   printf '#!/bin/sh\nsystemctl reload nginx\n' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
   sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
   systemctl list-timers certbot.timer
   sudo certbot renew --dry-run                 # "Congratulations, all simulated renewals succeeded"
   ```

### Nginx site

```bash
sudo cp ~/clipforge-bot/deploy/nginx/clipforge-bot.conf /etc/nginx/sites-available/clipforge-bot.conf
sudo ln -sf /etc/nginx/sites-available/clipforge-bot.conf /etc/nginx/sites-enabled/clipforge-bot.conf
sudo nginx -t && sudo systemctl reload nginx
curl -sI http://clipforge.thetirtayasa.my.id/ | head -1    # 301 to https
```

`nginx -t` must pass before the reload. `cannot load certificate` means step 3 did not finish;
`conflicting server name` means another site already declares this host name. Remove that
duplicate.

The Google Drive redirect URI (section 14) on this domain is
`https://clipforge.thetirtayasa.my.id/api/v1/integrations/drive/callback`. Use it in the Google
Cloud OAuth client and in `CLIPFORGE_DRIVE_REDIRECT_URI`, then restart `clipforge-api` and
`clipforge-worker` if it changed.

### Build and publish

Node.js ≥ 22.22 is needed once (for example `curl -fsSL https://deb.nodesource.com/setup_24.x | sudo bash -
&& sudo apt-get install -y nodejs`). Then, as your normal user:

```bash
cd ~/clipforge-bot && git pull && ./deploy/deploy_frontend.sh
curl -sI https://clipforge.thetirtayasa.my.id/ | head -1          # HTTP/2 200
curl -s https://clipforge.thetirtayasa.my.id/api/v1/health/live   # {"status":"ok"}
```

### Open it in Telegram

BotFather → your bot → *Bot Settings → Configure Mini App* (and *Menu Button*) → URL
`https://clipforge.thetirtayasa.my.id/`. Open the bot's menu button on your phone.

| Check | Expected |
|---|---|
| Open `https://clipforge.thetirtayasa.my.id/` in a normal browser | "Open from Telegram" |
| Open the Mini App from another Telegram account | "Access denied" |
| Open the Mini App from the admin account | Dashboard with job counts and estimated AI cost |
| Settings: change *Minimum score*, save | "Settings saved."; an out-of-range value shows the API's message under the field |
| Settings → Google Drive → Connect | Google consent opens outside Telegram; status turns `connected` after reopening |
| Videos → a video → upload a file; switch to airplane mode mid-upload, then back | "Upload paused"; *Resume upload* continues from the last chunk |
| Clips → a clip → *Open in Google Drive* (iOS and Android) | Drive opens outside Telegram (be signed in to the uploading Google account) |
| Clip: edit the title, Approve | Title shown in the list; original visible as "AI suggestion" |
| Affiliate: turn *Affiliate links* on, add a product, rematch a clip, approve a link | Suggestion with reason; *Copy caption* copies the caption with the link and disclosure |
| Affiliate links off | No review buttons on clips; the caption has no affiliate links |

## Updating

```bash
cd ~/clipforge-bot && git pull && sudo ./deploy/install.sh && ./deploy/deploy_frontend.sh
```

The script syncs code, reinstalls, migrates and restarts the API and the worker only when
something changed.

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
| Drive status `expired` / Telegram "Google Drive is disconnected" | The refresh token was revoked or expired (an app left in "Testing" expires them after 7 days). Reconnect (section 14), then `POST /jobs/{id}/retry` for jobs that failed in `uploading`. |
| `Google OAuth is not configured` (503) | One of `CLIPFORGE_GOOGLE_CLIENT_ID`, `CLIPFORGE_GOOGLE_CLIENT_SECRET`, `CLIPFORGE_DRIVE_REDIRECT_URI` missing; add it and restart `clipforge-api` and `clipforge-worker`. |
| Subtitles missing or in a fallback font | `fonts-dejavu-core` not installed; re-run `install.sh`. |
| Migration fails with permission errors | `migration.env` must use the Supabase `postgres` user, not `clipforge_app_prod`. |

## After verification

Tick the VPS items in `tasks/todo.md` (T07 and Checkpoint M1 for steps 1–10, T11 and Checkpoint
M2 for sections 11–12, Checkpoint M3 for section 13, T28 and Checkpoint M4 for section 14,
Checkpoint M5 for section 15), or
send the outputs for review.
