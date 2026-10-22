# Architecture Decision Records

Accepted decisions for ClipForge Bot. Source spec: [`docs/SPEC-001-ClipForge-Bot.md`](../SPEC-001-ClipForge-Bot.md).

New decisions get the next number (`adr-NNN.md`) and use the same sections: Status, Date, Related, Context, Decision, Consequences. A changed decision is recorded in a new ADR that supersedes the old one; the old ADR's status is updated to `Superseded by ADR-NNN`.

| ADR | Title | Status |
|---|---|---|
| [ADR-001](adr-001.md) | Backend layout, Python version and uv tooling | Accepted |
| [ADR-002](adr-002.md) | Alembic executes versioned raw SQL migrations | Accepted |
| [ADR-003](adr-003.md) | Async SQLAlchemy with psycopg 3 and separated database roles | Accepted |
| [ADR-004](adr-004.md) | Supabase connection mode and TLS | Accepted |
| [ADR-005](adr-005.md) | PostgreSQL-backed job queue with leases and fencing | Accepted |
| [ADR-006](adr-006.md) | Job stage state machine with durable stage artifacts | Accepted; stage list superseded by ADR-034 |
| [ADR-007](adr-007.md) | Error taxonomy and retry policy | Accepted |
| [ADR-008](adr-008.md) | Async subprocesses for media work | Accepted |
| [ADR-009](adr-009.md) | Provider-independent AI adapters with registry and job snapshot | Accepted |
| [ADR-010](adr-010.md) | Server-side scoring and pure candidate selection | Accepted |
| [ADR-011](adr-011.md) | Windowed analysis of long transcripts | Accepted |
| [ADR-012](adr-012.md) | Audio extraction and chunking | Accepted |
| [ADR-013](adr-013.md) | Clip rendering profile | Accepted; crop part superseded by ADR-033 |
| [ADR-014](adr-014.md) | Idempotent Google Drive uploads | Accepted |
| [ADR-015](adr-015.md) | Google Drive OAuth and token encryption | Accepted |
| [ADR-016](adr-016.md) | Telegram initData authentication and session tokens | Accepted |
| [ADR-017](adr-017.md) | Chunked resumable uploads | Accepted; evidence part superseded by ADR-031 |
| [ADR-018](adr-018.md) | Permitted URL downloads via domain allowlist and SSRF guard | Accepted |
| [ADR-019](adr-019.md) | YouTube discovery for Indonesia with dynamic trending categories | Accepted |
| [ADR-020](adr-020.md) | Affiliate product matching | Accepted |
| [ADR-021](adr-021.md) | Frontend stack for the Telegram Mini App | Accepted |
| [ADR-022](adr-022.md) | Observability: structured logs and redaction | Accepted |
| [ADR-023](adr-023.md) | Testing strategy | Accepted |
| [ADR-024](adr-024.md) | AI cost tracking via price snapshots | Accepted |
| [ADR-025](adr-025.md) | Generated clip titles from the analysis pass | Accepted |
| [ADR-026](adr-026.md) | Typed configurable settings with per-job snapshot | Accepted |
| [ADR-027](adr-027.md) | Free-tier AI providers: Gemini primary, OpenRouter fallback | Accepted |
| [ADR-028](adr-028.md) | Client-side AI quota guard | Accepted |
| [ADR-029](adr-029.md) | Clip preview via external Google Drive link | Accepted |
| [ADR-030](adr-030.md) | Continuous integration on GitHub Actions | Accepted |
| [ADR-031](adr-031.md) | Source registration without permission evidence | Accepted |
| [ADR-032](adr-032.md) | One upload at a time, queued in the Mini App | Accepted |
| [ADR-033](adr-033.md) | Subject-aware reframing with a blurred-background fallback | Accepted |
| [ADR-034](adr-034.md) | A running name for every pipeline stage | Accepted |
