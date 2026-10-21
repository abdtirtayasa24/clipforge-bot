# Backup and Restore

## What holds state

| State | Where | Loss means | Backup |
|---|---|---|---|
| Metadata: sources, videos, jobs and their events, candidates, clips, reviews, affiliate catalog and links, settings, AI usage, encrypted Drive token | Supabase PostgreSQL | Everything except the clip files | Supabase backups and a nightly `pg_dump` (below) |
| Clip files | Google Drive, folder **ClipForge Bot** | Rendered clips | Google Drive itself (trash keeps deleted files 30 days) |
| Secrets | `/etc/clipforge-bot/backend.env`, `migration.env`, `supabase-ca.crt` | The services can't start or reach the database | The password manager (escrow, below) |
| `CLIPFORGE_TOKEN_ENCRYPTION_KEY` | `backend.env` | The stored Drive token becomes unreadable; Drive must be reconnected (no data loss) | The password manager |
| Workspace (sources, audio, renders in progress) | `/var/lib/clipforge-bot/workspace` | In-flight jobs redo their stages | None needed: it is rebuilt from the sources, and uploaded sources must be uploaded again |
| Code, units, Nginx site | Git repository plus `deploy/` | — | GitHub |

## Secret escrow

When installing and after every rotation (`docs/security-checklist.md`), store these in the
password manager:

- every value in `backend.env`, `CLIPFORGE_TOKEN_ENCRYPTION_KEY` above all;
- the Supabase `postgres` password;
- the `clipforge_app_prod` password;
- the BotFather token;
- the Google OAuth client secret.

Never copy the env files to unencrypted storage.

## Database backups

### Supabase-managed

Supabase backs up projects on paid plans daily; Point-in-Time Recovery (PITR) is an add-on. The
retention and availability depend on the plan, so check *Project Settings → Database → Backups*
for what your project has. Restores from there replace the whole project database. Use them for
a disaster, then follow *After any restore* below.

### Own nightly dump (works on every plan)

A logical dump of the `public` schema, taken with the migration credentials and kept
root-only on the VPS. Copy it off the VPS regularly.

The PostgreSQL client must be at least the server's major version. Check the server with
`SELECT current_setting('server_version');` in the SQL editor. If Ubuntu's client is older,
install the matching one from the PostgreSQL apt repository (PGDG):

```bash
sudo apt-get install -y postgresql-common && sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt-get install -y postgresql-client-17            # the server's major version
```

Create the backup script `/usr/local/sbin/clipforge-backup`:

```bash
sudo tee /usr/local/sbin/clipforge-backup >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
dir=/var/backups/clipforge-bot
mkdir -p "$dir"
url="$(sed -n 's/^CLIPFORGE_MIGRATION_DATABASE_URL=//p' /etc/clipforge-bot/migration.env | tail -n 1)"
file="$dir/clipforge-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --dbname="$url" --schema=public --format=custom --no-owner --file="$file.part"
mv "$file.part" "$file"
find "$dir" -name 'clipforge-*.dump' -mtime +14 -delete
EOF
sudo chmod 700 /usr/local/sbin/clipforge-backup
```

Schedule it daily at 04:00, after discovery, and run it once by hand:

```bash
echo '0 4 * * * root /usr/local/sbin/clipforge-backup' | sudo tee /etc/cron.d/clipforge-backup
sudo /usr/local/sbin/clipforge-backup && sudo ls -l /var/backups/clipforge-bot
```

The dump contains the encrypted Drive token, never the key. Copy dumps off the VPS (for example
`scp` to a machine you control) at least weekly. Test a restore into a scratch Supabase project
once per quarter.

## Restore into a new Supabase project

This is for the case where the original project is gone or corrupted. The schema is created by
the migrations, which keeps the hardened grants and RLS of migration 004. Only the data comes
from the dump.

1. Create the new project and download its CA certificate (installation.md step 1). Point
   `migration.env` and `backend.env` at the new pooler host. Keep the old
   `CLIPFORGE_TOKEN_ENCRYPTION_KEY`.
2. Stop the services: `sudo systemctl stop clipforge-worker clipforge-api clipforge-discovery.timer`.
3. Run `sudo ./deploy/install.sh` to create the schema. Then create the runtime login
   (installation.md step 5).
4. Empty the rows the migrations seeded, so the dump's rows replace them. In the SQL editor of
   the **new** project, run both statements together. `job_events` is append-only by trigger,
   so triggers are switched off for this session:

   ```sql
   SET session_replication_role = replica;
   DO $$ DECLARE t TEXT; BEGIN
     FOR t IN SELECT tablename FROM pg_tables
              WHERE schemaname = 'public' AND tablename <> 'alembic_version' LOOP
       EXECUTE format('TRUNCATE TABLE %I CASCADE', t);
     END LOOP;
   END $$;
   ```

5. Load the data. Triggers are disabled for the session, so the append-only `job_events` and
   the foreign keys load in any order:

   ```bash
   url="$(sudo sed -n 's/^CLIPFORGE_MIGRATION_DATABASE_URL=//p' /etc/clipforge-bot/migration.env)"
   sudo pg_restore --list /var/backups/clipforge-bot/<dump> | grep -v alembic_version > /tmp/restore.list
   sudo pg_restore --data-only --use-list=/tmp/restore.list --file=/tmp/restore.sql /var/backups/clipforge-bot/<dump>
   sudo psql "$url" --single-transaction -v ON_ERROR_STOP=1 \
     -c 'SET session_replication_role = replica' -f /tmp/restore.sql
   sudo rm /tmp/restore.sql /tmp/restore.list
   ```

6. Continue with *After any restore*.

## After any restore

1. Re-check access control (`docs/security-checklist.md`, manual check 5), and that RLS is
   still enabled everywhere:

   ```sql
   SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity;   -- no rows
   ```

2. Release the leases of jobs that were running at the moment of the backup. Workers reclaim
   them on their own once the 5-minute leases expire, so this step only saves the wait:

   ```sql
   UPDATE processing_jobs SET lease_expires_at = now() WHERE status = 'running';
   ```

3. Start the services: `sudo systemctl start clipforge-api clipforge-worker clipforge-discovery.timer`.
   Then check `curl -s 127.0.0.1:8000/api/v1/health/ready`.
4. Mini App → **Settings → Google Drive** shows **connected**. If it shows *expired*, reconnect.
   That is needed if the encryption key was lost; a token issued after the backup also works.
5. Unfinished jobs whose workspace is gone (for example on a new VPS) fail visibly at their next
   stage, because their source or stage files are missing. For each such video, upload the
   source again and queue a new job. Clips that finished jobs already uploaded stay in Drive and
   remain reviewable.

## Google Drive

The clips stay in the admin's Drive under **ClipForge Bot**. Each file carries the app property
`clipforge_clip_id`, and its Drive id is stored on the clip. Restored metadata therefore points
at the same files. Files deleted from Drive by mistake can be restored from Drive's trash
within 30 days. Do not move the files out of the folder, or files the app did not create into
it: with the `drive.file` scope, the app sees only files it created itself.
