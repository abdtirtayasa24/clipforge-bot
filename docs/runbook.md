# Operations Runbook

Day-to-day operation of ClipForge Bot on the VPS. Installation: `docs/installation.md`.
Backups: `docs/backup-restore.md`. Secret rotation and security checks:
`docs/security-checklist.md`.

## Services

| Unit | Role | Restart policy |
|---|---|---|
| `clipforge-api.service` | FastAPI on `127.0.0.1:8000`, behind Nginx | `on-failure`, 5 s |
| `clipforge-worker.service` | Runs one job at a time through the pipeline | `always`, 10 s; `MemoryMax=1800M` |
| `clipforge-discovery.timer` → `.service` | YouTube discovery daily at 03:00, catches up after downtime | oneshot |

```bash
systemctl status clipforge-api clipforge-worker --no-pager
systemctl list-timers clipforge-discovery.timer
sudo systemctl restart clipforge-api            # e.g. after editing backend.env
sudo systemctl restart clipforge-worker         # the running job is abandoned and resumed (below)
sudo systemctl start clipforge-discovery.service   # discovery now (or Mini App: Sources → Run discovery now)
curl -s 127.0.0.1:8000/api/v1/health/ready      # database, workspace, disk, ffmpeg, ai_providers
```

Stopping or restarting the worker is always safe. The current stage is abandoned without
writing anything. The job keeps its lease, and after a restart it is reclaimed once the
5-minute lease expires. It resumes from its last finished stage, and verified artifacts,
transcripts and Drive uploads are reused (`docs/reliability-report.md`).

## Logs

Every service writes one JSON object per line to journald. Secrets, tokens and URL query
strings are redacted.

```bash
journalctl -u clipforge-worker -f                        # follow the worker
journalctl -u clipforge-api --since "-1 hour" --no-pager
journalctl -u clipforge-discovery --since yesterday --no-pager
journalctl -u 'clipforge-*' -p warning --since today     # warnings and errors only
journalctl -u clipforge-worker -o cat | grep '"job_id": "<job id>"'   # one job's lines
```

Useful messages:

- `job claimed`, `job reclaimed`, `job finished`, `job attempt failed`, `job failed`,
  `job deferred for quota`;
- `clip rendered`, `clip uploaded`;
- `lease lost; stage aborted`;
- `discovery finished` (it carries `failed_sources`).

## Jobs

### States

Job states: `awaiting_source` → `queued` → `running` → `completed` | `completed_no_clips` |
`failed` | `cancelled`, with `retry_scheduled` between attempts.

A job has 3 attempts. Errors are handled in one of three ways (ADR-007):

- **Transient** (network, 5xx, timeouts): retried with backoff.
- **Permanent** (rights, invalid media, provider rejects the request): fail at once.
- **Quota exhausted**: deferred until the provider's reset without using an attempt (a
  `quota_deferred` event).

Every failure records an event and sends a Telegram message.

### Retry, cancel

In the Mini App: **Jobs → job → Retry** (failed jobs only; resumes from the last finished stage)
or **Cancel** (a waiting job stops at once; a running one at its next checkpoint, between stages
or clips). The API equivalents are `POST /api/v1/jobs/{id}/retry` and `/cancel`.

### Stuck job

1. **Is the worker running?** Run `systemctl status clipforge-worker`. If it is not active,
   start it, and look at `journalctl -u clipforge-worker -n 100` for the reason.
2. **Is the job waiting by design?**

   ```sql
   SELECT status, stage, attempts, next_attempt_at, lease_expires_at, leased_by, last_error
   FROM processing_jobs WHERE id = '<job id>';
   SELECT event_type, stage, message, details, created_at FROM job_events
   WHERE job_id = '<job id>' ORDER BY id DESC LIMIT 10;
   ```

   | What you see | Meaning | Action |
   |---|---|---|
   | `retry_scheduled`, `next_attempt_at` in the future | Backoff or quota wait (`quota_deferred` event) | Wait. For quota waits, raise the RPD limit in *Settings → AI providers* if the provider allows it. |
   | `queued`, many jobs started in the last 24 h | `daily_job_cap` reached | Wait, or raise the cap in *Settings → Clip generation* |
   | `queued`, worker idle | Worker cannot claim (database errors) | `journalctl -u clipforge-worker` for `claiming a job failed`; check `health/ready` |
   | `running`, `lease_expires_at` in the past | The worker died mid-stage | It is reclaimed within seconds while a worker runs; otherwise start the worker |
   | `running`, lease renewing, same stage for hours | A slow stage (long transcription, Drive upload) | Follow the worker log. ffmpeg steps have timeouts; if one is truly hung, `sudo systemctl restart clipforge-worker` (safe, see above) |
   | `awaiting_source` | No authorized media yet | Upload the file or register a permitted URL for the video |

3. **Still stuck?** Cancel the job and queue a new one for the video. Never edit
   `processing_jobs` by hand while a worker runs: every job write is fenced by its lease
   (ADR-005).

### Failed uploads

When Telegram says "Google Drive is disconnected", or *Settings → Google Drive* shows
**expired**, reconnect Drive in the Mini App. Then **Retry** the jobs that failed in
`uploading`. Uploads resume from their persisted sessions, and files already in Drive are
adopted; no duplicates are created.

## Disk pressure

At least `reserved_disk_bytes` (default 5 GB) must stay free on `/var/lib/clipforge-bot`.

```bash
df -h /var/lib/clipforge-bot
sudo du -sh /var/lib/clipforge-bot/workspace/{sources,jobs} 2>/dev/null
```

- Uploads that would cross the reserve are refused with HTTP 507. URL downloads are deferred
  and fail after 3 attempts. `health/ready` reports `disk` when free space is below the reserve.
- After a job completes, its source and stage files are deleted; transcripts, analyses and
  clips not yet uploaded are kept. On each start, the worker removes the workspaces of jobs that
  ended more than `CLIPFORGE_WORKSPACE_GC_HOURS` (default 24) ago. To force that cleanup now:
  `sudo systemctl restart clipforge-worker`.
- Still short of space? Cancel or finish the queued work. Then check for other large consumers:
  `sudo journalctl --disk-usage` (shrink with `sudo journalctl --vacuum-size=500M`), old
  dumps in `/var/backups/clipforge-bot`, and the apt cache.
- Never delete files under `workspace/jobs/<id>` for a job that is `running` or
  `retry_scheduled`.

## Rotating secrets

See `docs/security-checklist.md` → *Secret rotation*. That covers the session secret, bot
token, Fernet key, OAuth client secret, API keys and the database password.

## Upgrading

Every release is a git tag `vX.Y.Z`. Read its notes for new `backend.env` variables first.

```bash
cd ~/clipforge-bot
git fetch --tags && git checkout vX.Y.Z
sudo ./deploy/install.sh          # sync code, uv pip install ., migrate, restart changed units
./deploy/deploy_frontend.sh       # Mini App
sudo ls -t /opt/clipforge-bot/releases/ | head -2    # new freeze record: <UTC time>-<commit>.txt
curl -s 127.0.0.1:8000/api/v1/health/ready
```

`install.sh` is idempotent. Units restart only when code, packages or unit files changed. The
running job is resumed after the worker restart, as above. Migrations run before the restart,
so keep them additive (new tables, columns, settings) for the old code to keep working during the
short window between migration and restart.

## Rollback to the previous release

1. Find the previous tag and its package record:

   ```bash
   cd ~/clipforge-bot && git tag --sort=-creatordate | head -3
   sudo ls -t /opt/clipforge-bot/releases/      # <UTC time>-<commit>.txt per installed state
   ```

2. **If the release added migrations, roll them back first, with the new code still checked
   out.** Only that code has the down migrations. List the revisions and downgrade to the
   previous tag's head:

   ```bash
   cd /opt/clipforge-bot/backend
   url="$(sudo sed -n 's/^CLIPFORGE_MIGRATION_DATABASE_URL=//p' /etc/clipforge-bot/migration.env)"
   sudo env CLIPFORGE_MIGRATION_DATABASE_URL="$url" .venv/bin/alembic history | head
   sudo systemctl stop clipforge-worker
   sudo env CLIPFORGE_MIGRATION_DATABASE_URL="$url" .venv/bin/alembic downgrade <previous head, e.g. 0008>
   ```

   Down migrations drop what the release added, including data in new tables or columns. Take
   a backup first (`sudo /usr/local/sbin/clipforge-backup`).

3. Check out the previous tag and reinstall:

   ```bash
   cd ~/clipforge-bot && git checkout <previous tag>
   sudo ./deploy/install.sh && ./deploy/deploy_frontend.sh
   ```

4. Verify that the installed packages match that release's record. `pyproject.toml` pins every
   dependency exactly, so they normally do:

   ```bash
   sudo -u clipforge env HOME=/opt/clipforge-bot VIRTUAL_ENV=/opt/clipforge-bot/backend/.venv \
     /usr/local/bin/uv pip freeze > /tmp/now.txt
   diff /tmp/now.txt /opt/clipforge-bot/releases/<that release's file>.txt && echo identical
   ```

   If they differ, restore that release's exact set, then restart:

   ```bash
   sudo -u clipforge env HOME=/opt/clipforge-bot VIRTUAL_ENV=/opt/clipforge-bot/backend/.venv \
     /usr/local/bin/uv pip install -r /opt/clipforge-bot/releases/<that release's file>.txt
   sudo systemctl restart clipforge-api clipforge-worker
   ```

5. Check `health/ready`, open the Mini App, and watch one job.

## Health checklist (weekly)

- `systemctl --failed` lists no `clipforge-*` unit, and
  `journalctl -u clipforge-discovery --since -7d | grep "discovery finished"` shows 7 runs.
- Mini App dashboard: no unexpected failed jobs. Free disk is above 5 GB, and AI cost is within
  budget.
- `sudo ls /var/backups/clipforge-bot`: daily dumps are present and copied off the VPS.
- `sudo certbot renew --dry-run` succeeds (the certificate renews automatically).
- *Settings → Google Drive* shows **connected**.
