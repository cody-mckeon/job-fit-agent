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

The operational workflows use the same scoring profile as the main CLI. Because the market uses inconsistent titles for AI automation and implementation work, exact target titles are retained but the scorer now favors broader role-family plus context matches. Current target families are:

- Product Management
- Forward Deployed Engineering
- AI Operations
- AI Automation
- AI Transformation
- Workflow Automation
- Business Systems
- Internal Tools
- Solutions Architecture / Solutions Engineering
- AI Implementation
- RevOps Automation / Revenue Systems
- Marketing Ops Automation / Marketing Systems
- Product Operations
- Platform Automation: ServiceNow, Moveworks, Power Platform, Workato

Automation-specific, platform-specific, business-systems, internal-tools, solutions, implementation, RevOps, Marketing Ops, and product-operations roles can appear as high-fit, near-fit, or apply-now candidates only when role viability and geography gates also pass and the posting pairs the title with AI, automation, workflow systems, internal tools, or product-systems context. Generic project/program management, public-sector project management, security program management, operations, consulting, support, account-management, sales enablement, campaign execution, CRM hygiene, reporting, sales admin, and lifecycle-marketing roles remain downranked unless the posting clearly includes AI implementation, automation systems, workflow systems, internal tools, product systems, agentic workflows, or enterprise automation ownership. Priority-company matches are ranking boosts only and do not replace role-family/context or geography guardrails.

## Run specific workflows

Review unapplied high-fit roles and run prep-next-application:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main unapplied-high-fit
make docker-prep-next
# or with Telegram handoff:
docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-next-application --min-score 75 --notify-telegram
```

Use `prep-next-application --min-score <n>` to prevent package generation for auto-selected jobs below the threshold; explicit `--job-id` selections below the threshold are blocked unless `--force` is supplied.

`unapplied-high-fit` keeps score and actionability separate: eligible high-fit roles appear first, geography-review roles stay in manual review, and ineligible international roles appear only when explicitly auditing. Add `--eligible-only`, `--include-ineligible`, `--limit <n>`, or `--json` when triaging from automation. For one-off geography investigations, run `python -m job_fit_agent.main debug-geography <job_id>` to inspect structured location fields, ignored noisy terms, final eligibility, red flags, and viability reasons as JSON.

If a job exists online but is missing from local SQLite, prepare directly from the Ashby job page with `prep-url`. This path is useful when an Ashby board API collector returns `403` but the direct posting still renders:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-url "https://jobs.ashbyhq.com/elevenlabs/275f43d0-b62d-401d-830c-7c1ac0e688aa" --notify-telegram
```

`prep-url` inserts or updates the job, scores it, generates the application package, exports the resume PDF unless `--skip-pdf` is passed, and zips the package when the normal package flow succeeds. By default it blocks geography-review, geography-ineligible, and otherwise non-actionable jobs; use `--force` only when Cody intentionally wants the package despite warnings.

After Cody submits an application, mark it immediately so it is not recommended again:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main mark-applied --job-id <id> --note "Applied through Ashby using generated package."
docker compose run --rm job-fit-agent python -m job_fit_agent.main applied
docker compose run --rm job-fit-agent python -m job_fit_agent.main pipeline
```

Application lifecycle decisions are durable in the tracked `data/application_status.json` file keyed by stable job key. Company-level application cooldowns/blocks are durable in `data/company_application_blocks.json` and suppress related roles from that company until Cody uses a recruiter/manual-review strategy or the temporary block expires. Company-centered Opportunity Pipeline strategy is durable in `data/opportunity_pipeline.json`; it consumes job scores and application state without replacing or overwriting the Ashby/Greenhouse/Lever job scoring engine. Supported lifecycle statuses are `not_applied`, `saved`, `applied`, `interviewing`, `rejected`, `offer`, `withdrawn`, `skipped`, and `blocked`; each status change appends `status_history` and writes lifecycle timestamps such as `applied_at`, `interviewing_at`, `rejected_at`, `offer_at`, `withdrawn_at`, `skipped_at`, `saved_at`, `blocked_at`, and `updated_at`. The local `data/jobs.sqlite` row id is runtime/local state and may differ across Cody's Mac, Telegram, and GitHub Actions. For Telegram/GitHub Actions, use the stable command from the package whenever possible, for example `applied ashby:elevenlabs:a3097257-a07a-4a7e-b9fe-b8555c1a0fa7`; mobile aliases are convenient shortcuts, but stable keys are safest across machines. After a Telegram status update commits back to GitHub, run `git pull` locally before triage so digest and prep use the latest durable status store. Rejected and blocked jobs remain tracked for learning, analytics, and relationship strategy, but are excluded from active application recommendations.



Work Opportunity Engine commands:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main work-opportunities
docker compose run --rm job-fit-agent python -m job_fit_agent.main discover-w2 --source-file data/w2_seeds.json --limit 25
docker compose run --rm job-fit-agent python -m job_fit_agent.main discover-contracts --source-file data/contracts.json --limit 25
docker compose run --rm job-fit-agent python -m job_fit_agent.main discover-rfps --query "AI workflow automation RFP" --location "Nevada" --limit 10
docker compose run --rm job-fit-agent python -m job_fit_agent.main discover-local-businesses --query "manual operations workflow pain" --location "Las Vegas" --limit 10
docker compose run --rm job-fit-agent python -m job_fit_agent.main discover-relationships --source-file data/relationship_map.json --limit 25
docker compose run --rm job-fit-agent python -m job_fit_agent.main add-work-opportunity --title "AI workflow automation pilot" --company "Local hospitality group" --type local_business --source manual --priority high --status qualify --why-fit "Operations workflow automation and AI agent deployment" --next-action "Research pain points and prepare diagnostic outreach"
docker compose run --rm job-fit-agent python -m job_fit_agent.main add-rfp --title "AI operations workflow RFP" --organization "County Innovation Office" --deadline 2099-06-15 --source government --priority high --why-fit "Agent deployment and workflow automation for internal operations"
docker compose run --rm job-fit-agent python -m job_fit_agent.main opportunity-review
docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-rfp <opportunity_id>
docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-1099 <opportunity_id>
docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-local-outreach <opportunity_id>
```

Use this layer for all work Cody could pursue, not only W2 roles: 1099 contracts, fractional roles, RFPs, vendor opportunities, local business opportunities, relationship opportunities, and manual leads from podcasts, LinkedIn, local events, or conversations. Discovery commands support `--source-file`, `--query`, `--location`, and `--limit`; records are durable in `data/work_opportunities.json` with fit, actionability, urgency, revenue, relationship, and recommended-next-action fields. `opportunity-review` compares W2 jobs, company-universe records, Opportunity Pipeline strategy, Work Opportunity Engine records, blocked companies, deadlines, relationship value, urgency, actionability, revenue potential, and fit score, then recommends the highest-leverage next action today. A W2 company cooldown does not automatically prevent separate 1099, RFP, vendor, or local-business strategy for that organization.

Opportunity Pipeline strategy commands:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main opportunity-pipeline
docker compose run --rm job-fit-agent python -m job_fit_agent.main pipeline-review
docker compose run --rm job-fit-agent python -m job_fit_agent.main set-company-status elevenlabs blocked_cooldown "Wait until 2026-09-02 or pursue recruiter/manual review"
docker compose run --rm job-fit-agent python -m job_fit_agent.main set-company-status linear relationship_strategy "Manual review Product Manager and AI Product Engineer roles before applying"
docker compose run --rm job-fit-agent python -m job_fit_agent.main set-company-status stripe watch "Weak current fit, watch for AI operations, internal tools, or agent deployment roles"
```

Use this layer when the question is **"What should Cody do next?"** rather than **"Is this specific job a fit?"** It groups companies into Apply now, Relationship strategy, Blocked / cooldown, Research targets, Watchlist, and Skip. `pipeline-review` returns **Best next action today** and prefers high-priority companies with high-fit or strong near-fit roles in Cody's AI agents/workflow automation/AI operations/product systems lane; if no strong apply-now role exists, it recommends relationship or research work instead of forcing a weak application.

Post-application lifecycle examples:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main telegram-command "interviewing ashby:company:external-id Recruiter screen scheduled"
docker compose run --rm job-fit-agent python -m job_fit_agent.main telegram-command "rejected ashby:company:external-id Rejected after application"
docker compose run --rm job-fit-agent python -m job_fit_agent.main telegram-command "offer ashby:company:external-id"
docker compose run --rm job-fit-agent python -m job_fit_agent.main telegram-command "withdrawn ashby:company:external-id Accepted another role"
docker compose run --rm job-fit-agent python -m job_fit_agent.main telegram-command "blocked ashby:company:external-id Ashby 90-day application limit, recruiter/manual review needed"
docker compose run --rm job-fit-agent python -m job_fit_agent.main telegram-command "block-company elevenlabs --days 90 Ashby 90-day application limit, recruiter/manual review only"
docker compose run --rm job-fit-agent python -m job_fit_agent.main block-company elevenlabs "Ashby 90-day application limit, recruiter/manual review only" --days 90
docker compose run --rm job-fit-agent python -m job_fit_agent.main blocked
python -m job_fit_agent.main unblock-expired
docker compose run --rm job-fit-agent python -m job_fit_agent.main rejected
docker compose run --rm job-fit-agent python -m job_fit_agent.main outcomes
```

If Cody decides not to apply, mark it skipped with a reason:

```bash
docker compose run --rm job-fit-agent python -m job_fit_agent.main mark-skipped --job-id <id> --reason "Not US eligible"
# or by URL:
docker compose run --rm job-fit-agent python -m job_fit_agent.main mark-skipped --url <job_url> --reason "DACH role"
```

Role score measures title/skills/role-family fit, not application readiness. Actionability additionally requires high/near fit, `apply_now` or `strong_review` viability, `geographic_eligibility=eligible`, a real job URL, no applied/skipped/rejected/withdrawn/offer/blocked application status, and no active, unexpired company-level block in `data/company_application_blocks.json`. High role fit does not override geography gating: geography-review, geography-ineligible, and non-US-region roles are excluded from default auto-selection, actionable digest sections, Telegram package auto-selection, and daily Telegram recommendations. Digest separates `Actionable apply-now roles`, `Strong role fit, geography not eligible`, and `Needs geography review`. Opportunity Pipeline sits above this raw score/actionability layer and can recommend recruiter outreach, referral work, research, or watchlist status when direct apply is blocked or strategically weak. To intentionally prepare a geography-review or company-blocked job, select it explicitly with `--job-id <id>` and use `--include-review` for geography review or `--force` for deliberate overrides; force may warn that the job is blocked/non-actionable.

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

Application prep uses role-family project selection for generated resume strategy, draft, cover letter, and recruiter note. Enterprise solutions, forward deployed, AI transformation, workflow automation, and internal tools roles prioritize AI Product Design Operating System, Job Fit Agent, and RWLV Priority Governor Agent; product manager roles prioritize AI Product Design Operating System, RWLV Priority Governor Agent, and Job Fit Agent; analytics/product systems roles prioritize RWLV Priority Governor Agent, AI Product Design Operating System, and Job Fit Agent. Summary copy should use concrete AI-enabled workflow systems language rather than buzzword-heavy AI-native defaults.

The separate **Job Status Command** workflow persists one explicit Telegram status command by committing `data/application_status.json` back to `main` with `Update application status` after a successful command. The **Process Telegram Commands** workflow (`.github/workflows/process-telegram-commands.yml`) is the polling command-and-control path: it runs `python -m job_fit_agent.main process-telegram-updates`, reads recent Bot API updates for `TELEGRAM_CHAT_ID`, ignores update ids already captured in `data/telegram_processed_updates.json`, sends Telegram confirmations, and commits changed durable data files with `Process Telegram status commands`. It intentionally does not require `data/jobs.sqlite` changes; the SQLite database remains local/runtime job cache state.

Scheduled GitHub Actions now installs `pandoc` plus LaTeX dependencies (`texlive-latex-base`, `texlive-latex-recommended`, `texlive-fonts-recommended`, `lmodern`) and attempts resume PDF export before packaging. `setspace.sty` is provided by `texlive-latex-recommended`. When export succeeds, the PDF is included in both the Telegram zip and the GitHub artifact; when export fails, the workflow continues and `submit_resume.md` remains the manual fallback.

Download package artifacts from: **GitHub → Actions → Job Fit Agent → latest run → Artifacts**.
Telegram package summaries include a direct **GitHub Actions run URL** (when executed in Actions), so you can open the run from Telegram, go to **Artifacts**, and download the application package.
Telegram now also sends the application package `.zip` directly in the chat for mobile download.
Mobile workflow: **Telegram → download zip → review files → submit manually**.
GitHub Actions artifact upload remains unchanged as backup storage.
Local computer does not need to be on for scheduled runs, and final application submission remains manual.

Placeholder/test URLs are filtered out of actionable recommendations (digest default sections, prep-next-application auto-select, and Telegram notifications). Applied, skipped, blocked, saved, and company-blocked jobs are filtered out as well, and digest includes summary counts for unapplied high-fit, applied, skipped, and blocked roles. Geography-review and geography-ineligible jobs are also excluded from actionable defaults; high role-fit geography-review jobs belong in `Needs geography review`, and high role-fit international jobs belong in `Strong role fit, geography not eligible`, not scheduled auto-prep.

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
rejected linear-product-manager Rejected after application
reject linear-product-manager
interviewing linear-product-manager Recruiter screen scheduled
interview linear-product-manager
offer linear-product-manager
withdrawn linear-product-manager
withdraw linear-product-manager
applied ashby:linear:b7669c4b-eeca-421d-ba9a-d90203f6fcb2
rejected ashby:linear:b7669c4b-eeca-421d-ba9a-d90203f6fcb2 Rejected after application
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
