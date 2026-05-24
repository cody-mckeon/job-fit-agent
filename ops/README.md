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

## Run specific workflows

Run prep-next-application:

```bash
make docker-prep-next
# or with Telegram handoff:
docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-next-application --notify-telegram
```

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
- `python -m job_fit_agent.main prep-next-application --skip-browser --skip-pdf --notify-telegram`

Scheduled GitHub Actions uses `--skip-pdf` to avoid failures when `pandoc` is unavailable on hosted runners. Desktop/local Docker/VPS runs can still export resume PDFs when `pandoc` and LaTeX dependencies are present. `submit_resume.md` is always generated and remains the fallback for manual submission.

Placeholder/test URLs are filtered out of actionable recommendations (digest default sections, prep-next-application auto-select, and Telegram notifications).

Set repository secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Docker/VPS remains the future multi-client runtime pattern.
