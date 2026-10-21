# Installation Guide

This guide installs ClipForge Bot on a fresh Ubuntu VPS, from accounts to the first processed
video, in one pass. Follow the sections in order. `deploy/README.md` holds the same commands
with per-checkpoint verification and troubleshooting.

| Component | Where it runs |
|---|---|
| API (`clipforge-api`), worker (`clipforge-worker`), daily discovery (`clipforge-discovery.timer`) | VPS, systemd, user `clipforge` |
| Telegram Mini App (static files) and the `/api/` proxy | VPS, Nginx, `https://clipforge.thetirtayasa.my.id/` |
| Database | Supabase PostgreSQL (project `clipforge-bot`) |
| Clip storage | Google Drive of the admin (folder **ClipForge Bot**) |
| AI | Gemini (primary), OpenRouter (analysis fallback) |

Paths: code `/opt/clipforge-bot/backend/`, Mini App `/var/www/clipforge-bot/`, workspace
`/var/lib/clipforge-bot/workspace/`, secrets `/etc/clipforge-bot/`.

## 0. Requirements

- Ubuntu 22.04 or 24.04 VPS: 2 vCPU, 4 GB RAM, 20–30 GB disk, sudo, outbound HTTPS, ports 80
  and 443 reachable, Nginx installed. At least 5 GB must stay free: that is the default reserve.
- A DNS name for the Mini App. This guide uses `clipforge.thetirtayasa.my.id`; replace it
  everywhere (including `deploy/nginx/clipforge-bot.conf`) if yours differs.
- A password manager for the secrets created below.

## 1. Accounts and credentials

Collect these before touching the VPS. Write each secret straight into the password manager.

### Supabase

1. Create the project `clipforge-bot` and choose a strong database password. This is the
   `postgres` user's password, used only for migrations.
2. *Connect → Session pooler*: note the host `aws-0-<REGION>.pooler.supabase.com`, port
   `5432` and the project ref (ADR-004: the session pooler, not the transaction pooler).
3. *Project Settings → Database → SSL Configuration → Download certificate*: save the CA file.
4. Choose a second strong password for the runtime login `clipforge_app_prod`. It is created in
   step 6.

### Telegram

1. @BotFather → `/newbot` → note the **bot token**.
2. Your numeric Telegram user id (for example from @userinfobot): you become the administrator.

### Google Cloud (one project you own)

1. **YouTube Data API v3**: *APIs & Services → Library* → enable it. Then *Credentials → Create
   credentials → API key*, and restrict the key to the YouTube Data API v3.
2. **Google Drive API**: enable it. Under *OAuth consent screen* choose user type External, add
   your Google account as a test user, and add the scope
   `https://www.googleapis.com/auth/drive.file`. Then **Publish app** (status "In production").
   While an app is in "Testing", Google expires refresh tokens after 7 days. `drive.file` is a
   non-sensitive scope, so publishing needs no verification.
3. *Credentials → Create credentials → OAuth client ID*: type "Web application", authorized
   redirect URI `https://clipforge.thetirtayasa.my.id/api/v1/integrations/drive/callback`.
   Note the client id and the client secret.

### AI providers

- **Gemini**: Google AI Studio → *Get API key*. Note the per-model RPM/RPD limits AI Studio
  shows for your project; you enter them in step 13.
- **OpenRouter** (analysis fallback): create a key at openrouter.ai.

On free tiers the providers may use submitted audio, transcripts and frames to improve their
products (ADR-027). Process only media whose authorization allows that.

## 2. Clone and first run

```bash
git clone https://github.com/abdtirtayasa24/clipforge-bot.git ~/clipforge-bot
cd ~/clipforge-bot
git checkout <release tag>        # e.g. v1.0.0; omit to install main
sudo ./deploy/install.sh
```

The first run installs the system packages:

- ffmpeg and the DejaVu subtitle font;
- git, curl, rsync and build tools;
- uv 0.11.7.

It also creates the `clipforge` account and the directories, copies both env templates into
`/etc/clipforge-bot/`, then stops with `fill in real values and re-run`. That stop is expected.

## 3. Secrets

```bash
sudo install -o root -g clipforge -m 0640 ~/prod-ca-2021.crt /etc/clipforge-bot/supabase-ca.crt
sudo -e /etc/clipforge-bot/migration.env
sudo -e /etc/clipforge-bot/backend.env
```

**`migration.env`** (only `install.sh` reads it): replace `PROJECT_REF`, `REGION` and
`CHANGE_ME` with the `postgres` password. URL-encode special characters in passwords:

| Character | Encoded |
|---|---|
| `?` | `%3F` |
| `@` | `%40` |
| `#` | `%23` |
| `%` | `%25` |
| `&` | `%26` |

Keep `sslmode=verify-full&sslrootcert=…`.

**`backend.env`** (read by the services):

| Variable | Value |
|---|---|
| `CLIPFORGE_DATABASE_URL` | Template URL with `clipforge_app_prod.<ref>` and its password |
| `CLIPFORGE_TELEGRAM_BOT_TOKEN` | BotFather token |
| `CLIPFORGE_SESSION_SECRET` | `openssl rand -hex 32` |
| `YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | Keys from step 1 |
| `CLIPFORGE_GOOGLE_CLIENT_ID`, `CLIPFORGE_GOOGLE_CLIENT_SECRET` | OAuth client from step 1 |
| `CLIPFORGE_DRIVE_REDIRECT_URI` | `https://clipforge.thetirtayasa.my.id/api/v1/integrations/drive/callback` |
| `CLIPFORGE_TOKEN_ENCRYPTION_KEY` | `python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |

Store `CLIPFORGE_TOKEN_ENCRYPTION_KEY` and every other value in the password manager
(docs/backup-restore.md). The script refuses to continue while any `CHANGE_ME` remains.

## 4. Install and migrate

```bash
sudo ./deploy/install.sh
```

This syncs the backend to `/opt/clipforge-bot/backend` and creates the venv. It runs
`uv pip install .` and records `uv pip freeze` in `/opt/clipforge-bot/releases/`. It applies
every migration and installs the systemd units. It also sets both env files to their required
owner and mode (`root:clipforge 0640` and `root:root 0600`).

Check the migration state:

```bash
cd /opt/clipforge-bot/backend
sudo env CLIPFORGE_MIGRATION_DATABASE_URL="$(sudo sed -n 's/^CLIPFORGE_MIGRATION_DATABASE_URL=//p' /etc/clipforge-bot/migration.env)" \
  .venv/bin/alembic current           # 0008 (head)
```

## 5. Runtime database login

Migration 004 created the `NOLOGIN` group role `clipforge_app`. In the Supabase SQL editor,
create the login with the password from step 1:

```sql
CREATE ROLE clipforge_app_prod LOGIN PASSWORD '<runtime password>' IN ROLE clipforge_app;
```

## 6. Administrator

```bash
sudo systemd-run --pipe --wait --quiet --uid=clipforge \
  --property=EnvironmentFile=/etc/clipforge-bot/backend.env \
  --working-directory=/opt/clipforge-bot/backend \
  /opt/clipforge-bot/backend/.venv/bin/python -m clipforge.admin_cli add-admin \
  --telegram-id <your telegram id> --name "<your name>"
```

Expected: a JSON log line `"msg": "admin enabled"`.

## 7. Start the services

```bash
sudo systemctl enable --now clipforge-api clipforge-worker clipforge-discovery.timer
curl -s 127.0.0.1:8000/api/v1/health/live      # {"status":"ok"}
curl -s 127.0.0.1:8000/api/v1/health/ready     # 200, every check "ok"
systemctl list-timers clipforge-discovery.timer   # next run 03:00
journalctl -u clipforge-worker -n 5 --no-pager    # "worker started"
```

A `503` from `ready` lists the failing checks; see *Troubleshooting* in `deploy/README.md`.

## 8. Domain, certificate and Nginx

```bash
dig +short clipforge.thetirtayasa.my.id                     # the VPS IP (A/AAAA records)
sudo ufw allow 'Nginx Full'                                  # if ufw is active
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot certonly --nginx -d clipforge.thetirtayasa.my.id --agree-tos -m <email> --no-eff-email
printf '#!/bin/sh\nsystemctl reload nginx\n' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo certbot renew --dry-run
sudo cp ~/clipforge-bot/deploy/nginx/clipforge-bot.conf /etc/nginx/sites-available/clipforge-bot.conf
sudo ln -sf /etc/nginx/sites-available/clipforge-bot.conf /etc/nginx/sites-enabled/clipforge-bot.conf
sudo nginx -t && sudo systemctl reload nginx
```

The site has its own server blocks and certificate, so other sites on the same Nginx are
untouched.

## 9. Mini App

Install Node.js 22.22 or newer once:

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo bash - && sudo apt-get install -y nodejs
```

Then build and publish as your normal user:

```bash
cd ~/clipforge-bot && ./deploy/deploy_frontend.sh
curl -sI https://clipforge.thetirtayasa.my.id/ | head -1          # HTTP/2 200
curl -s https://clipforge.thetirtayasa.my.id/api/v1/health/live   # {"status":"ok"}
```

## 10. BotFather Mini App

In @BotFather, open your bot → *Bot Settings*:

- *Configure Mini App* → *Enable* → URL `https://clipforge.thetirtayasa.my.id/`.
- *Menu Button* → the same URL, with a label such as "ClipForge".

Open the bot on your phone and tap the menu button. You should see the dashboard; any other
account sees "Access denied".

## 11. First configuration (in the Mini App)

1. **Settings → Google Drive → Connect Google Drive**: approve in the browser with the Google
   account that should own the clips. Reopen the screen; the status shows **connected**.
2. **Settings → AI providers**: for each selected model, enter the RPM/RPD limits from AI Studio
   and check the prices (used only for cost estimates).
3. **Sources → New source**: add `Trending` with region `ID`, and any monitored channels
   (24-character id starting with `UC`). Then tap **Run discovery now**.
4. **Settings → Downloads**: permitted download domains, only if you ingest from URLs.
5. **Settings → Clip generation**: minimum score and durations. Set **Daily job cap** from the
   benchmark (docs/benchmark-report.md) once it has been run; until then leave it empty, which
   means no cap.
6. **Settings → Notifications**: on (the default).

## 12. First job (smoke test)

1. **Sources → All videos** → a 30–120 minute video you are authorized to process → upload the
   file (or register a permitted URL) with the permission evidence → **Queue processing job**.
2. **Jobs**: the job moves through the stages `validated` → … → `uploading` → `completed`.
   How long that takes depends on the video's length and the AI quotas; the benchmark measures
   it.
3. Telegram shows "N clips ready for review". **Clips** lists them; *Open in Google Drive*
   plays a clip.

The installation is complete. Day-to-day operation is in `docs/runbook.md`; backups are in
`docs/backup-restore.md`.
