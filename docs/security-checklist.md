# Security Checklist (T41, Milestone M6)

**Automated:** `cd backend && python -m pytest -m security` runs every test tagged `security`.
CI runs these tests as part of the full suite. The **manual** rows below are checked on the VPS
and recorded in [Manual results](#manual-vps-results).

## Review — 2026-10-21

The repository was reviewed against the T41 scope, and the branch changes were run through
`/security-review` (multi-step, with false-positive filtering). **No high or critical findings
are open.** That run found no vulnerabilities at confidence ≥ 8.

| Area | How it is enforced | Evidence |
|---|---|---|
| Every privileged route needs an admin session | `require_admin` on every router except `/health*`, `/auth/telegram` and the Drive OAuth callback (state + PKCE protected). A route-table test fails if a new route is missing it. | `tests/unit/test_route_table.py::test_every_non_public_route_requires_an_admin` |
| initData signature, staleness, replay | HMAC per the Bot API docs. `auth_date` must be at most `CLIPFORGE_INIT_DATA_MAX_AGE_SECONDS` (default 1 h) old and at most 60 s in the future. Tampered, foreign-bot, duplicate-field and malformed data is rejected. | `tests/unit/test_telegram_auth.py`, `tests/integration/test_auth_api.py` |
| Session tokens | HMAC-SHA256 with a secret of 32+ characters and a 12 h expiry. The admin must still be active on every request. | `tests/unit/test_telegram_auth.py`, `test_auth_api.py::test_private_route_rejects_*` |
| Rate limiting | 10 failed logins per 5 min per client IP return 429. The client IP is taken from Nginx's `X-Forwarded-For`; Uvicorn trusts only `127.0.0.1` and reads the right-most untrusted hop, so clients cannot spoof it. | `test_auth_api.py::test_repeated_failures_are_rate_limited` |
| DB roles / grants / RLS | `clipforge_app` is NOLOGIN, DML only, with no DDL, ownership or `BYPASSRLS`. `anon` and `authenticated` have no table access and cannot create tables. RLS is enabled on every table. | `tests/integration/test_security_roles.py` |
| SSRF | Permitted-URL ingestion allows only HTTPS on allow-listed hosts and never YouTube. Every resolved address, on every connection and redirect, must be public. Downloads have a size cap. | `tests/unit/test_url_guard.py`, `tests/integration/test_download.py` |
| Secrets absent from logs | A JSON formatter redacts tokens, keys, `initData`, `Authorization`, cookies, URL query strings and the bot token in Bot API paths. An end-to-end audit captures every line from login, Drive OAuth and a full pipeline run with Drive and Telegram, and checks none contains a secret. | `tests/unit/test_logging.py`, `tests/e2e/test_log_audit.py`, `test_auth_api.py::test_init_data_and_tokens_never_logged` |
| Secrets at rest | The Drive refresh token is Fernet-encrypted. No API key is stored in the database (`secret_ref` names an environment variable). The status API never returns token material. | `tests/unit/test_crypto.py`, `test_drive_oauth.py::test_callback_stores_the_refresh_token_encrypted`, `::test_status_reports_the_connection_without_token_material` |
| Env file permissions | `install.sh` sets `backend.env` to `root:clipforge 0640` and `migration.env` to `root:root 0600` on **every** run, not only when it creates them. | `tests/unit/test_deploy_files.py::test_install_script_keeps_env_files_private` |
| Transport / headers | TLS 1.2+, HSTS, `nosniff`, `Referrer-Policy: no-referrer` and `server_tokens off` on every response, API included. No X-Frame-Options, because Telegram Web frames the Mini App. | `test_deploy_files.py::test_nginx_site_sends_security_headers_on_every_response` |
| Services | Units run as `clipforge` with `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, and write access only to `/var/lib/clipforge-bot`. The API listens on `127.0.0.1` only. | `test_deploy_files.py::test_api_unit_is_hardened` and the related unit tests |
| Remote DB TLS | A remote database URL without `sslmode=verify-full` is refused at startup. | `test_deploy_files.py::test_remote_database_urls_require_verified_tls`, `tests/unit/test_db_session.py` |

### Changes made in this review

- `install.sh` now enforces env-file owner and mode on every run. Before, a file widened by an
  editor or `cp` stayed world-readable.
- Nginx sends HSTS, `nosniff`, `Referrer-Policy` and `server_tokens off`. The per-location
  `add_header Cache-Control` lines were replaced by `expires`, because a location-level
  `add_header` silently drops the server-level headers.
- A Drive refresh token that cannot be decrypted (for example after a key rotation) is now
  treated as "Drive disconnected". The admin is notified at once and the status shows
  `expired`, instead of the job running into generic retries.

### Accepted risks

| Risk | Why it is accepted | Mitigation |
|---|---|---|
| A captured `initData` can be replayed until it is 1 h old | Telegram's `initData` has no nonce. Making it single-use would break re-login from the same app session. | Sent only over TLS to our API and never logged. Lower `CLIPFORGE_INIT_DATA_MAX_AGE_SECONDS` to shorten the window. |
| Single session tokens cannot be revoked | Stateless tokens. | Deactivating the admin takes effect on the next request; rotating `CLIPFORGE_SESSION_SECRET` revokes all sessions. |
| Login rate limit is per process and in memory | One API process (`--workers 1`). | The limit resets on restart, which needs root. |
| Stale-worker Drive duplicate in a sub-second window | See `docs/reliability-report.md`. | The lease is renewed right before each new upload session. |

## Secret rotation

All secrets live in `/etc/clipforge-bot/backend.env`. Edit it with
`sudo -e /etc/clipforge-bot/backend.env`, then run `sudo ./deploy/install.sh` to re-apply the
file's owner and mode. Finally restart the units that read the secret:
`sudo systemctl restart clipforge-api clipforge-worker`. Discovery reads it on its next timer run.

| Secret | Procedure | Effect |
|---|---|---|
| `CLIPFORGE_SESSION_SECRET` | `openssl rand -hex 32`, replace, restart the API. | All sessions end; the Mini App logs in again automatically from `initData`. |
| `CLIPFORGE_TELEGRAM_BOT_TOKEN` | BotFather → `/revoke`, replace, restart all units. | Old initData stops verifying; reopen the Mini App. |
| `CLIPFORGE_TOKEN_ENCRYPTION_KEY` (Fernet) | See below. | Drive must be reconnected once. |
| `CLIPFORGE_GOOGLE_CLIENT_SECRET` | Google Cloud console → OAuth client → add secret, replace, restart, then disable the old secret. | No reconnect needed (the refresh token stays valid). |
| `YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | Create a new key in the provider console, replace, restart the worker and discovery, then delete the old key. | None. |
| Database password (`clipforge_app_prod`) | `ALTER ROLE clipforge_app_prod PASSWORD '<new>'` in the Supabase SQL editor, update `CLIPFORGE_DATABASE_URL`, restart. | Short reconnect. |

### Fernet key rotation (`CLIPFORGE_TOKEN_ENCRYPTION_KEY`)

The key encrypts one value: the Google Drive refresh token in `oauth_credentials`. Rotating the
key therefore means storing a freshly issued token under the new key:

1. Pause new uploads (optional): `sudo systemctl stop clipforge-worker`.
2. Generate the key with
   `python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.
   Store it in the password manager next to the old one, then replace the value in `backend.env`.
3. Run `sudo ./deploy/install.sh` (re-applies the file's mode), then
   `sudo systemctl restart clipforge-api` and `sudo systemctl start clipforge-worker`.
4. Mini App → *Settings → Google Drive*: status shows **expired**. Tap **Connect Google Drive**
   and approve. The new refresh token is encrypted with the new key, and status shows
   **connected**.
5. Retry any job that failed or waits on Drive (*Jobs → Retry*). Uploads resume from their
   persisted sessions.
6. Revoke the old grant at <https://myaccount.google.com/permissions> if it is still listed,
   then delete the old key from the password manager.

If step 4 is skipped, jobs that reach `uploading` report "Google Drive is disconnected" in
Telegram and retry; nothing is uploaded twice.

## Manual VPS checks

Run these after deploying the T41 commit, and record the results below.

```bash
# 1. Env file permissions
sudo stat -c '%U:%G %a %n' /etc/clipforge-bot/backend.env /etc/clipforge-bot/migration.env
#    expected: root:clipforge 640 and root:root 600

# 2. Headers (from any machine)
curl -sI https://clipforge.thetirtayasa.my.id/ | grep -iE 'strict-transport|nosniff|referrer|server:|cache-control'
curl -sI https://clipforge.thetirtayasa.my.id/api/v1/health/live | grep -iE 'strict-transport|nosniff'

# 3. Unauthenticated access is refused
curl -s -o /dev/null -w '%{http_code}\n' https://clipforge.thetirtayasa.my.id/api/v1/jobs      # 401
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer forged' \
  https://clipforge.thetirtayasa.my.id/api/v1/settings                                    # 401

# 4. Log audit after a full day of use (login, Drive connect, at least one completed job).
#    Each secret value is read from the env file, never typed; prints only the variable name
#    and a match count.
sudo bash -c 'set -a; . /etc/clipforge-bot/backend.env; set +a
for name in CLIPFORGE_TELEGRAM_BOT_TOKEN CLIPFORGE_SESSION_SECRET CLIPFORGE_GOOGLE_CLIENT_SECRET \
            CLIPFORGE_TOKEN_ENCRYPTION_KEY YOUTUBE_API_KEY GEMINI_API_KEY OPENROUTER_API_KEY; do
  value="${!name}"; [ -n "$value" ] || continue
  count=$(journalctl -u "clipforge-*" --since "-2 days" -o cat | grep -cF -- "$value")
  echo "$name: $count"
done
journalctl -u "clipforge-*" --since "-2 days" -o cat \
  | grep -ciE "bearer [a-z0-9]|init_?data=|refresh_token\":\s*\"1//|ya29\." || true'
#    expected: every count 0

# 5. Runtime role cannot run DDL (Supabase SQL editor, or psql as clipforge_app_prod)
#    SET ROLE clipforge_app; CREATE TABLE t (x int);       -> permission denied
#    Supabase Data API with the anon key:
curl -s "https://<project>.supabase.co/rest/v1/processing_jobs?select=id" \
  -H "apikey: <anon key>"                                   # [] or a permission error, never rows
```

### Manual VPS results

| Check | Date | Result |
|---|---|---|
| 1. Env file permissions | | *pending* |
| 2. Security headers | | *pending* |
| 3. Unauthenticated access refused | | *pending* |
| 4. journald secret audit | | *pending* |
| 5. Runtime role DDL / anon blocked | | *pending* |
