# Docker operations

This project can run in a container for repeatable local development, Codespaces use, and future VPS deployments. Local Docker is optional because Docker validation can run in GitHub Actions via `.github/workflows/docker-validate.yml`.

## Local Docker usage

1. Copy environment template:

```bash
cp .env.example .env
```

2. Build image:

```bash
make docker-build
```

3. Run digest pipeline:

```bash
make docker-digest
```


## GitHub Actions Docker validation

Use the Docker validation workflow when local Docker is unstable or unavailable.

- The workflow file is `.github/workflows/docker-validate.yml`.
- It runs on pushes to `main` and on pull requests that touch Docker/runtime-relevant files.
- You can manually trigger it with **workflow_dispatch** to validate builds and runtime behavior on demand.
- In CI, if `.env` is missing, Actions copies `.env.example` to `.env` and then appends CI-safe overrides required by Docker Compose (`JOB_FIT_ENABLE_LEVER=false`, `CLIENT_ID=github-actions`, empty Telegram fields).
- The workflow prints only masked env values for debugging (`KEY=***`) and never commits a real `.env` file.

## Docker image dependencies

The Docker image includes PDF export dependencies: `pandoc`, `texlive-latex-base`, `texlive-latex-recommended`, `texlive-fonts-recommended`, `lmodern`, `fonts-liberation`, and `ca-certificates`.


## Target role families

The operational workflows use the same scoring profile as the main CLI. Current target families are:

- Product Management
- Forward Deployed Engineering
- AI Operations
- AI Automation
- Workflow Automation
- AI Transformation
- Internal Tools Product Management
- AI Enablement
- Platform Automation: ServiceNow, Moveworks, Power Platform, Workato
- RevOps/Marketing Ops Automation when automation-heavy

Automation-specific and platform-specific roles can appear as high-fit, near-fit, or apply-now candidates only when role viability and geography gates also pass. Generic operations, consulting, support, account-management, campaign execution, CRM hygiene, reporting, sales admin, and lifecycle-marketing roles remain downranked unless the posting clearly includes AI, automation, internal tools, workflow systems, product systems, agentic workflows, or enterprise automation ownership.

## Run specific workflows

Review unapplied high-fit roles and run prep-next-application:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main unapplied-high-fit
make docker-prep-next
# or with Telegram handoff:
docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-next-application --notify-telegram
```

`unapplied-high-fit` shows eligible high-fit roles first and geography-review roles in a separate section. Add `--eligible-only`, `--include-ineligible`, `--limit <n>`, or `--json` when triaging from automation.

After Cody submits an application, mark it immediately so it is not recommended again:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main mark-applied --job-id <id> --note "Applied through Ashby using generated package."
docker compose run --rm job-fit-agent python -m job_fit_agent.main applied
```

Application decisions are durable in the tracked `data/application_status.json` file keyed by stable job key. The local `data/jobs.sqlite` row id is runtime/local state and may differ across Cody's Mac, Telegram, and GitHub Actions. For Telegram/GitHub Actions, use the stable command from the package whenever possible, for example `applied ashby:elevenlabs:a3097257-a07a-4a7e-b9fe-b8555c1a0fa7`; mobile aliases are convenient shortcuts, but stable keys are safest across machines. After a Telegram status update commits back to GitHub, run `git pull` locally before triage so digest and prep use the latest durable status store.

If Cody decides not to apply, mark it skipped with a reason:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main mark-skipped --job-id <id> --reason "Not US eligible"
# or by URL:
docker compose run --rm job-fit-agent python -m job_fit_agent.main mark-skipped --url <job_url> --reason "DACH role"
```

Auto-prep requires both strong role fit and acceptable geography. High role fit does not override geography gating: geography-review, geography-ineligible, and non-US-region roles are excluded from default auto-selection and actionable digest sections. Jobs marked `application_status=applied` or `application_status=skipped` in SQLite or `data/application_status.json` are also excluded from default digest actionable sections, prep-next auto-selection, and daily Telegram recommendations. To intentionally prepare a geography-review job, select it explicitly with `--job-id <id> --force`; forced Telegram summaries warn that geography requires manual review before applying.

Telegram handoff env vars:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Run an interactive CLI command in container:

```bash
make docker-run
# Then replace command if needed, for example:
# docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-next-application
```

## Lever collector toggle

Lever collection is disabled by default. Enable in `.env`:

```dotenv
JOB_FIT_ENABLE_LEVER=true
```

## Persistent data behavior

Default `docker-compose.yml` is optimized for local/test runtime and only persists generated outputs:

- `./data -> /app/data`
- `./applications -> /app/applications`

The container image uses repository-copied files for `/app/config` and `/app/profile` during default build/test runs.


## Client-specific config/profile overrides

For multi-client usage, add client-specific compose overrides instead of changing the default compose file.

Example override file: `ops/clients/example/docker-compose.override.yml`

```bash
docker compose \
  -f docker-compose.yml \
  -f ops/clients/example/docker-compose.override.yml \
  run --rm job-fit-agent pytest
```

Override mounts (read-only):

- `./config:/app/config:ro`
- `./profile:/app/profile:ro`

Keep `data/` and `applications/` as persistent runtime outputs for each client environment.

## Secrets and source control

Do not commit `.env` or production credentials. Keep secrets in per-environment env files or secret managers.

---

## Future VPS client-isolation pattern

When running multiple clients on one VPS, isolate each client with dedicated env and Compose project naming.

Suggested layout:

```text
clients/
  acme/
    .env
  beta/
    .env
```

Recommended practices:

- Use a per-client env file (`clients/<client_name>/.env`).
- Use a per-client compose project name (for example `COMPOSE_PROJECT_NAME=jobfit_acme`).
- Use client-specific bind mounts or Docker volumes for `data` and `applications` outputs.
- Use separate Telegram bot tokens/chat IDs per client.
- Keep client config/profile/data separated so scoring, notifications, and generated artifacts are isolated.

Example command pattern:

```bash
docker compose --project-name jobfit_acme --env-file clients/acme/.env up -d
```


## Current background runner

GitHub Actions is the current background runner for daily automation; local machines do not need to remain powered on.

Daily scheduled flow includes Telegram package summary handoff:
- `pytest`
- `rm -f data/jobs.sqlite data/jobs.sqlite-shm data/jobs.sqlite-wal` (clears test/runtime DB state before production steps)
- `python -m job_fit_agent.main run`
- `python -m job_fit_agent.main rescore`
- `python -m job_fit_agent.main digest`
- `python -m job_fit_agent.main prep-next-application --skip-browser --notify-telegram`
- Uploads generated `applications/` package as a GitHub Actions artifact (`job-fit-application-package-<run_id>`, retained 14 days)

The separate **Job Status Command** workflow persists Telegram status commands by committing `data/application_status.json` back to `main` with `Update application status` after a successful command. It intentionally does not require `data/jobs.sqlite` changes; the SQLite database remains local/runtime job cache state.

Scheduled GitHub Actions now installs `pandoc` plus LaTeX dependencies (`texlive-latex-base`, `texlive-latex-recommended`, `texlive-fonts-recommended`, `lmodern`) and attempts resume PDF export before packaging. `setspace.sty` is provided by `texlive-latex-recommended`. When export succeeds, the PDF is included in both the Telegram zip and the GitHub artifact; when export fails, the workflow continues and `submit_resume.md` remains the manual fallback.

Download package artifacts from: **GitHub → Actions → Job Fit Agent → latest run → Artifacts**.
Telegram package summaries include a direct **GitHub Actions run URL** (when executed in Actions), so you can open the run from Telegram, go to **Artifacts**, and download the application package.
Telegram now also sends the application package `.zip` directly in the chat for mobile download.
Mobile workflow: **Telegram → download zip → review files → submit manually**.
GitHub Actions artifact upload remains unchanged as backup storage.
Local computer does not need to be on for scheduled runs, and final application submission remains manual.

Placeholder/test URLs are filtered out of actionable recommendations (digest default sections, prep-next-application auto-select, and Telegram notifications). Applied and skipped jobs are filtered out as well, and digest includes summary counts for unapplied high-fit, applied, and skipped roles. Geography-review and geography-ineligible jobs are also excluded from actionable defaults; high role-fit geography-review jobs belong in manual review, not scheduled auto-prep.

Set repository secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Docker/VPS remains the future multi-client runtime pattern.

## Phase 2 serverless Telegram command bridge

Cody can mark jobs from Telegram without an always-on bot server. Telegram sends bot updates to a Cloudflare Worker webhook; the Worker validates the secret token and Cody's chat id, allowlists a status command, and calls GitHub's `repository_dispatch` API. GitHub Actions then runs the status update in the repository and sends a Telegram confirmation.

Supported commands from Telegram:

```text
applied linear-product-manager
/applied linear-product-manager
mark applied linear-product-manager
skip linear-product-manager Not US eligible
/skip linear-product-manager Not US eligible
save linear-product-manager
/save linear-product-manager
applied ashby:linear:b7669c4b-eeca-421d-ba9a-d90203f6fcb2
applied https://jobs.ashbyhq.com/linear/b7669c4b-eeca-421d-ba9a-d90203f6fcb2
applied 19
```

Local equivalents:

```bash
python -m job_fit_agent.main applied <job_id>
python -m job_fit_agent.main skip <job_id> "<reason>"
python -m job_fit_agent.main save <job_id>
python -m job_fit_agent.main telegram-command "applied linear-product-manager"
```

Reference Worker files live in `ops/telegram-worker/`.

Required Cloudflare Worker secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_ID`
- `TELEGRAM_WEBHOOK_SECRET`
- `GITHUB_OWNER`
- `GITHUB_REPO`
- `GITHUB_DISPATCH_TOKEN`

Required GitHub Actions secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Security expectations:

- Validate `X-Telegram-Bot-Api-Secret-Token`.
- Validate `TELEGRAM_ALLOWED_CHAT_ID`.
- Only dispatch supported commands.
- Never return GitHub or Telegram tokens in responses.

Limitations:

- GitHub Actions startup means confirmations are delayed.
- The command workflow can only update jobs present in the persisted job database.
- If the runtime uses artifacts/caches instead of a committed `data/jobs.sqlite`, keep the command workflow aligned with that persistence pattern rather than adding a second state store.
