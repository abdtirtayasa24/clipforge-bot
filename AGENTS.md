# AGENTS.md — ClipForge Bot Contributor Rules

These rules apply to **every contributor, AI agent or human**. Always follow the working rules, naming conventions, code style and commit message format below. If a rule blocks you, raise it instead of working around it.

`CLAUDE.md` only points to this file (and imports it for Claude Code), so every agent reads the same rules. Edit `AGENTS.md` only.

## Where things live

| What | Where |
|---|---|
| Product spec (source of truth) | `docs/SPEC-001-ClipForge-Bot.md` |
| Architecture decisions | `docs/adr/` (index: `docs/adr/README.md`, files `adr-NNN.md`) |
| Implementation plan | `tasks/plan.md` |
| Task list with acceptance criteria | `tasks/todo.md` |

Before changing behavior covered by an ADR, read that ADR. To change a decision, add a new ADR that supersedes it; don't edit an accepted ADR's decision in place.

## Working Rules

### Python tooling: uv venv + uv pip, `pyproject.toml` only

`backend/pyproject.toml` is the single source of dependencies: `[project.dependencies]` for runtime and `[project.optional-dependencies].dev` for tooling. There are no `requirements*.txt` files and no `uv.lock` (ADR-001).

From `backend/`:

```bash
uv venv --python 3.12                 # create backend/.venv
.venv\Scripts\activate                # Windows
source .venv/bin/activate             # Linux / macOS
uv pip install -e ".[dev]"            # development and CI
uv pip install .                      # production VPS
```

- Add a dependency by writing it into `pyproject.toml` with an exact pin (`name==x.y.z`), then re-run `uv pip install -e ".[dev]"`.
- Run tools from the activated venv: `python -m pytest`, `ruff`, `mypy`, `alembic`, `uvicorn`.
- Never use bare `pip`, `uv add`, `uv sync` or `uv lock`.

### Shell commands: use `rtk`

Run commands through `rtk` to keep output compact, e.g. `rtk git status`, `rtk git diff`, `rtk test python -m pytest`, `rtk ruff check .`, `rtk err mypy clipforge`, `rtk read <file>`, `rtk grep <pattern>`.

### Codebase navigation: graphify first

- Query the knowledge graph before grepping or scanning files: `graphify query "<question>"`, `graphify explain "<node>"`, `graphify path "<A>" "<B>"`, `graphify affected "<node>"`. Read only the files the graph points to.
- `graphify-out/` is local-only and gitignored. Build it once per clone with `/graphify .`, then install hooks with `graphify hook install`. That command also writes a `.gitattributes` merge-driver line and a `merge.graphify` git config entry; both are unused for a local-only graph, so remove them (`rm .gitattributes && git config --remove-section merge.graphify`) and never commit `.gitattributes` for graphify.
- The git `post-commit` / `post-checkout` hooks rebuild the graph for **code** changes only. After changing docs (spec, ADRs, plan, runbooks) run `/graphify . --update`.

### Definition of done for every change

All of these pass before a change is committed:

- Backend (venv active, from `backend/`): `rtk ruff check .` · `rtk ruff format --check .` · `rtk err mypy clipforge` · `rtk test python -m pytest`
- Frontend (from `frontend/`): `rtk npm run lint` · `rtk tsc --noEmit` · `rtk vitest run` · `rtk npm run build`
- The task's own acceptance criteria and verification in `tasks/todo.md`.

CI runs the same checks (ADR-030). Never weaken a test to make it pass.

### Secrets and data

- Secrets come only from environment variables (`/etc/clipforge-bot/backend.env` in production). Never commit real values; `.env.example` / `backend.env.example` hold placeholders only.
- Never log tokens, `initData`, `Authorization` headers, refresh tokens, cookies, or URL query strings (ADR-022).
- Tests never touch Supabase or paid/live providers unless marked `-m live` (ADR-023).

## Naming Conventions

### Project names (from the spec)

| Component | Name |
|---|---|
| Repository | `clipforge-bot` |
| Project | ClipForge Bot |
| Python package | `clipforge` |
| Linux service prefix | `clipforge-` (`clipforge-api.service`, `clipforge-worker.service`, `clipforge-discovery.service`, `clipforge-discovery.timer`) |
| Linux service account | `clipforge` |
| Supabase project | `clipforge-bot` (the physical database keeps its provider name, usually `postgres`) |
| Runtime database role | `clipforge_app` |
| Paths | `/opt/clipforge-bot/backend/`, `/var/www/clipforge-bot/`, `/var/lib/clipforge-bot/workspace/`, `/etc/clipforge-bot/backend.env` |

### Code and files

| Item | Convention | Example |
|---|---|---|
| Python modules / packages | `snake_case` | `clipforge/queue/repository.py` |
| Python classes, Protocols, Pydantic models | `PascalCase` | `TranscriptionProvider`, `ClipCandidate` |
| Python functions, variables | `snake_case` | `claim_next`, `lease_token` |
| Constants | `UPPER_SNAKE_CASE` | `DEFAULT_MIN_SCORE` |
| Environment variables | `CLIPFORGE_` prefix, `UPPER_SNAKE_CASE` (third-party keys keep their standard names) | `CLIPFORGE_DATABASE_URL`, `GEMINI_API_KEY` |
| Database tables / columns | plural `snake_case` tables, `snake_case` columns | `processing_jobs.lease_version` |
| SQL migrations | `NNN_snake_case.sql` + `NNN_snake_case_down.sql` | `006_affiliate_links.sql` |
| Alembic revisions | `NNNN_snake_case.py` matching the SQL file | `0006_affiliate_links.py` |
| API paths | under `/api/v1`, plural nouns, `kebab-case` | `/api/v1/clips/{id}/affiliate-links` |
| Pipeline stages | one module per stage in `clipforge/pipeline/stages/` | `transcribe.py` |
| AI adapters | `<provider>_<capability>.py` | `gemini_transcription.py` |
| Tests | `tests/{unit,integration,e2e,reliability}/test_<subject>.py` | `tests/unit/test_scoring.py` |
| React components / pages | `PascalCase.tsx` | `ClipDetail.tsx` |
| Hooks / utilities (TS) | `camelCase.ts`, hooks start with `use` | `useJobs.ts`, `chunkedUpload.ts` |
| ADRs | `docs/adr/adr-NNN.md` | `docs/adr/adr-031.md` |

## Code Style

### General

- Follow existing patterns in neighboring files before introducing new ones; keep changes small and focused on the task.
- No speculative features, abstractions or configurability beyond the task and spec.
- **Minimal comments.** Let the code describe itself through clear names for functions, variables, classes and modules, plus small focused functions. Don't add a comment when better naming or structure would make the code clear. Write a comment only when it is critical and the code needs extra explanation it can't express itself, such as a non-obvious reason, a trade-off, an external constraint (spec, API or provider quirk) or a subtle invariant. Explain *why*, never restate *what* the code does. Remove comments that become outdated.
- Use `pathlib` for paths and never build filesystem paths from user input (uploads use UUID names).

### Python

- Formatting and linting: `ruff format` + `ruff check` (config in `pyproject.toml`); type checking: `mypy --strict` on `clipforge/`.
- Full type hints on all functions; no untyped `Any` without a reason.
- Pydantic v2 models at API and provider boundaries; SQLAlchemy 2.0 typed models (`Mapped[...]`).
- Async I/O for network, database and subprocess work; ffmpeg/ffprobe through `asyncio.create_subprocess_exec` (ADR-008).
- Raise errors from the taxonomy (`PermanentError`, `TransientError`, `LeaseLost`, `Cancelled`) rather than ad-hoc exceptions (ADR-007).
- All job state writes go through the fenced repository (ADR-005).
- Logging through the shared JSON logger with context fields; no `print` (ADR-022).

### TypeScript / React

- `strict` TypeScript, ESLint clean, no `any`.
- Server state through TanStack Query; API calls only through the typed client in `frontend/src/api/`.
- Use Telegram theme CSS variables; no UI kit (ADR-021).
- Keep accessibility basics: labels, button semantics, readable loading/error/empty states.

### Tests

- Every logic change comes with tests at the right level (unit for pure logic, integration for DB/queue, e2e for pipeline flows).
- External HTTP mocked with `respx`; media fixtures generated by ffmpeg; no network in default test runs.

## Commit Message Format

- **One line only**: no body, no trailers, no blank lines.
- Format: `<type>: <summary>` with `type` ∈ `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `build`, `perf`.
- Summary in English, imperative mood, lowercase start, no trailing period, ≤ 72 characters total.
- **No AI attribution of any kind**: no `Co-Authored-By: Claude …`, no "Generated with Claude Code", no tool or agent names.
- One logical change per commit.

Examples:

```text
feat: add fenced job claiming with lease renewal
fix: reject stale telegram initData
docs: add adr for drive upload reconciliation
```
