# Benchmark Report (T42, Milestone M6)

**Question (spec, Gathering Results):** can the VPS (2 vCPU, 4 GB RAM, 20–30 GB disk) process
5, 10 or 20 videos/day sustainably? This report answers it from measurements taken while the
VPS processes at least 5 real, authorized videos. It also sets the `daily_job_cap` setting.

**Status: pending — run on the VPS by the operator.** Only the production VPS, real source
videos and the configured AI providers give meaningful numbers. The development machine uses
synthetic media and fake providers.

## Tooling

- `python -m clipforge.tools.benchmark_report` reads finished jobs from the database:
  - per-stage latency from `job_events`, measured per attempt, with waits between retries
    excluded;
  - wall time per job and upload duration (the `uploading` stage);
  - clips per source;
  - estimated AI cost and AI requests per job and model from `provider_usage`, checked against
    each model's requests-per-day limit;
  - peak worker memory, peak disk use, lowest free disk and worker CPU share, from a resource
    log sampled from systemd accounting (`--resources`).

  It reports capacity by worker time (one job at a time, 80 % busy, p90 wall time) and by AI
  quota, whether 5/10/20 per day is sustainable, and a recommended cap. Tests:
  `tests/unit/test_benchmark_report.py` and `tests/e2e/test_benchmark_report.py`.
- **`daily_job_cap` setting** (new in T42; empty means no limit): it limits how many jobs
  *start* per rolling 24 hours. Jobs over the cap stay `queued` and start as soon as the window
  frees a slot. Jobs that already started (retries, reclaims after a crash) always continue.
  Set it in **Mini App → Settings → Clip generation → Daily job cap**; the change is audited in
  `app_setting_events`. Tests: `test_queue.py::test_daily_cap_holds_new_jobs_but_lets_started_ones_finish`,
  `test_settings_api.py::test_daily_job_cap_is_off_by_default_and_can_be_set_or_cleared`.

## Procedure (VPS)

Prerequisites: the T42 commit is deployed (`sudo ./deploy/install.sh`), Drive is connected, and
5–10 authorized long videos (30–120 min, the realistic mix) are registered (deploy/README.md
section 12).

1. Start the resource sampler in a second terminal and leave it running for the whole batch:

   ```bash
   while sleep 10; do
     printf '%s mem=%s peak=%s free=%s cpu=%s\n' "$(date +%T)" \
       "$(systemctl show clipforge-worker -p MemoryCurrent --value)" \
       "$(systemctl show clipforge-worker -p MemoryPeak --value)" \
       "$(df -B1 --output=avail /var/lib/clipforge-bot | tail -1)" \
       "$(systemctl show clipforge-worker -p CPUUsageNSec --value)"
   done | tee ~/t42-resources.log
   ```

2. Note the start time (`date -Is`), then queue all the jobs at once from the Mini App
   (*Sources → All videos → video → Queue processing job*). The worker runs them one after
   another, which is the real daily pattern.
3. When every job is `completed`, `completed_no_clips` or `failed`, stop the sampler and
   produce the report:

   ```bash
   sudo cp ~/t42-resources.log /tmp/t42-resources.log && sudo chmod 644 /tmp/t42-resources.log
   sudo systemd-run --pipe --wait --quiet --uid=clipforge \
     --property=EnvironmentFile=/etc/clipforge-bot/backend.env \
     /opt/clipforge-bot/backend/.venv/bin/python -m clipforge.tools.benchmark_report \
     --since "<start time>" --resources /tmp/t42-resources.log > ~/t42-report.md
   ```

   `systemd-run` reads `backend.env` exactly like the services do (deploy/README.md section 7).
4. Paste `~/t42-report.md` below under *Results*. Review each clip and record the share rated
   usable; the spec target is ≥ 70 %, tracked again in the pilot.
5. Set **Daily job cap** in the Mini App to the recommended value, or lower it if the AI cost
   per day is above budget. Record the value and the date below.

## Results

*(paste the tool output here)*

## Decision

| Item | Value |
|---|---|
| Videos measured | *pending* |
| Sustainable videos/day (5 / 10 / 20) | *pending* |
| Recommended `daily_job_cap` | *pending* |
| `daily_job_cap` set in settings on | *pending* |
| Clips rated usable | *pending* |
