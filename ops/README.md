# Docker operations

This project can run in a container for repeatable local development, Codespaces use, and future VPS deployments.

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

## Docker image dependencies

The Docker image includes PDF export dependencies: `pandoc`, `texlive-latex-base`, `texlive-latex-recommended`, `texlive-fonts-recommended`, `lmodern`, `fonts-liberation`, and `ca-certificates`.

## Run specific workflows

Run prep-next-application:

```bash
make docker-prep-next
```

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
