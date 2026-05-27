# job-fit-agent

`job-fit-agent` collects roles from company boards, normalizes job data, and feeds them into fit scoring.

## Supported sources

The shared collection pipeline supports these hosted job-board sources:

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{company}/jobs`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{company}`
- Lever: `https://jobs.lever.co/{company}`

All supported sources are stored in the same SQLite repository and flow through the same scoring, viability, geographic eligibility, notification, and digest logic.

## Company watchlist

Configured in `config/company_watchlist.yaml` by source. Use each provider's board token as the company value.

```yaml
greenhouse:
  - stripe

ashby:
  - anthropic

lever:
  - example-company
```

For a Lever board such as `https://jobs.lever.co/ramp`, add `ramp` under `lever`:

```yaml
lever:
  - ramp
```

## Run

```bash
python -m job_fit_agent.main
```

Learn a company from a manually found job URL:

```bash
python -m job_fit_agent.main learn-url "https://jobs.ashbyhq.com/scrunch/abc123"
python -m job_fit_agent.main learn-url "https://job-boards.greenhouse.io/robotsandpencils/jobs/5227395008"
python -m job_fit_agent.main learn-url "https://jobs.lever.co/ramp/abc123"
```

This command parses the source/company from the URL, fetches that company board, scores all jobs, persists them to SQLite, and adds the company to `config/discovery_queue.yaml`.
`discovery_queue.yaml` is for discovered companies not yet promoted to the permanent `config/company_watchlist.yaml`.

Promote a discovered company to the daily monitored watchlist:

```bash
python -m job_fit_agent.main promote-discovery ashby scrunch
python -m job_fit_agent.main promote-discovery lever ramp
```

Suggested workflow:
1. `learn-url` to ingest and score a newly found company board.
2. `digest` to review scored jobs.
3. `promote-discovery` to move the company from discovery queue into permanent watchlist monitoring.

The CLI runs enabled collectors, prints source-specific successful/failed companies, and aggregates jobs into a shared scoring pipeline.

Lever is disabled by default. To enable Lever at runtime, set the feature flag before running the CLI:

```bash
export JOB_FIT_ENABLE_LEVER=true
python -m job_fit_agent.main
```

## Scoring profile boosts

`config/target_profile.yaml` supports additional ranking boosts:

- `industry_bias`: optional list of industry/domain terms matched against job title, description, department, team, and company.
- `local_priority_companies`: optional list of higher-priority local/target companies.

These are **ranking boosts, not hard filters**. They improve ordering among already-relevant roles and do not replace role-family and location guardrails.

## Targeting evolution: AI-native product builder/operator

The scoring profile now targets not only traditional PM roles, but also adjacent AI-native builder/operator paths:

- AI builder
- Product engineering
- Workflow automation
- AI operations
- Developer tools
- Forward deployed engineering

Primary target role set includes:

- Product Manager
- Technical Product Manager
- AI Product Manager
- Forward Deployed Engineer
- Forward Deployed Product Engineer
- Forward Deployed AI Engineer

The system boosts jobs with strong agentic/workflow/orchestration/tooling capability signals while still keeping hard guardrails against pure infrastructure/backend-heavy engineering roles.


## Company discovery workflow

- `config/discovery_terms.yaml` = search intent for discovery providers.
- `config/discovery_seed_companies.yaml` = real companies to evaluate (manual/static provider input).
- `data/discovered_companies.yaml` = discovery review queue with status.

New discoveries are **not** automatically added to the main watchlist.

```bash
python -m job_fit_agent.main discover-companies
python -m job_fit_agent.main approve-company <company>
python -m job_fit_agent.main reject-company <company>
```

Workflow:
1. `discover-companies`
2. Review `data/discovered_companies.yaml`
3. `approve-company <company>` to promote known-source companies into `config/company_watchlist.yaml`
4. `python -m job_fit_agent.main run`

Discovery `source_guess` values may be `ashby`, `greenhouse`, `lever`, or `unknown`. Approved companies with a known Lever source are added to the `lever` section of `config/company_watchlist.yaml`.

Unknown-source discoveries stay in review (approved status only) until their source is known.

## Docker

For containerized local/Codespaces/VPS-oriented workflows, see `ops/README.md` for setup, commands, and client isolation patterns.

Default `docker-compose.yml` keeps only runtime output mounts (`data/`, `applications/`). `config/` and `profile/` come from files copied into the image for consistent test/build behavior. Client-specific `config/` and `profile/` bind mounts should be added only through compose override files.

## Test

```bash
python -m pytest
```

## Telegram notifications for high-fit jobs

Configure Telegram notifications in `config/notifications.yaml`:

```yaml
telegram:
  enabled: false
  bot_token: ""
  chat_id: ""
```

When enabled, the pipeline sends one Telegram message per **new high-fit job** only.
It does not notify for near-fit, duplicate, or updated jobs.

You can also provide credentials via environment variables (used as fallback when YAML values are empty):

```bash
export TELEGRAM_BOT_TOKEN="<your-bot-token>"
export TELEGRAM_CHAT_ID="<your-chat-id>"
```

Setup steps:
1. Create a bot with `@BotFather` and copy the bot token.
2. Start a chat with your bot (or add it to a group).
3. Obtain the chat ID (for groups, use the negative group ID format).
4. Set `enabled: true` in `config/notifications.yaml` and run the pipeline.

## Application workflow lifecycle

Each job in SQLite has a workflow status to track progress from discovery to close-out.

Valid statuses:
- `new`
- `interested`
- `applying`
- `applied`
- `interviewing`
- `rejected`
- `archived`

New jobs default to `new`. Re-scores update scoring fields but preserve your existing workflow status.

Set a status:

```bash
python -m job_fit_agent.main set-status <job_id> <status>
# example
python -m job_fit_agent.main set-status 8 interested
```

List jobs by status:

```bash
python -m job_fit_agent.main list-status interested
```

Digest output includes status on each job. You can optionally group digest output by status:

```bash
python -m job_fit_agent.main digest --group-by-status
```

Prepare an application package for a specific saved job:

```bash
python -m job_fit_agent.main prep-application <job_id>
# example
python -m job_fit_agent.main prep-application 8
```

This creates `applications/<company>_<role_slug>_<job_id>/` with:
- `fit_summary.md`
- `resume_strategy.md`
- `resume_draft.md`
- `cover_letter.md`
- `recruiter_note.md`
- `answer_bank.md`
- `risk_flags.md`

Extract application questions from the job URL:

```bash
python -m job_fit_agent.main extract-application-questions <job_id>
```

Extract application questions in-browser (recommended for Ashby apply flows that only render questions after clicking Apply):

```bash
pip install playwright
playwright install chromium
python -m job_fit_agent.main extract-application-questions-browser <job_id>
# optional debug artifacts
python -m job_fit_agent.main extract-application-questions-browser <job_id> --debug
```

Generate answers only for saved application questions:

```bash
python -m job_fit_agent.main generate-application-answers <job_id>
```

Export an upload-ready resume PDF for a prepared application package:

```bash
python -m job_fit_agent.main export-resume-pdf <job_id>
```

Generated filename format:
- `Cody_McKeon_<Company>_<Role>_Resume.pdf`
- Example: `Cody_McKeon_Linear_Product_Manager_Resume.pdf`

Compatibility note: if `resume_draft.md` is missing but `tailored_resume_draft.md` exists, the exporter uses the legacy file and prints a regeneration reminder.

Recommended application prep workflow:
1. `python -m job_fit_agent.main digest`
2. `python -m job_fit_agent.main prep-next-application`
   - Optional Telegram handoff:
     `python -m job_fit_agent.main prep-next-application --notify-telegram`
   - Optional explicit job selection with Telegram:
     `python -m job_fit_agent.main prep-next-application --job-id <id> --notify-telegram`
   - When using `--job-id`, prep is blocked by default if the job is non-actionable (`low_fit`, `skip`, `ineligible`, or `applied/rejected/archived`).
     Use `--force` to override intentionally:
     `python -m job_fit_agent.main prep-next-application --job-id <id> --force`
   - `--dry-run` output includes an `actionable` field so you can verify if the selected job is actionable before prep.
   - `--skip-pdf` keeps fast local/dev runs by skipping `pandoc` PDF export while still generating markdown outputs (`submit_resume.md`, `cover_letter.md`, `answer_bank.md`).
   - Note: job IDs are local database IDs and can differ between environments/machines.
3. Review/edit package artifacts
4. Submit manually
5. `python -m job_fit_agent.main set-status <job_id> applied`

Telegram handoff requires:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Location extraction debugging and audit

Use `debug-ashby-url` when you are investigating a single Ashby job page extraction issue in detail:

```bash
python -m job_fit_agent.main debug-ashby-url "https://jobs.ashbyhq.com/<company>/<job-id>"
```

Use `location-audit` when you want a scalable cross-company audit of stored jobs to find location extraction/normalization gaps:

```bash
python -m job_fit_agent.main location-audit
```

`location-audit` prints:
- Blank `location_raw` needing debugging by source/company with sample URLs.
- Region-only `location_raw` values by source/company (e.g. Europe, EMEA, APAC, LATAM, North America).
- Conflicting metadata (e.g. remote+hybrid signal conflicts, remote geography mismatches).
- Top sample URLs to debug next.
- Known source limitations (e.g. Ashby/Cursor blank `location_raw` where metadata is unavailable upstream).

Some job boards do not consistently expose complete location metadata in HTML, JSON-LD, or hydration/app-data payloads. In known cases (currently Ashby/Cursor), blank location metadata is treated as a source limitation and is routed to manual review instead of being treated as an extraction/parser failure.


## GitHub Actions daily runner

GitHub Actions is the current always-on background runner, so your local computer does not need to stay on for daily processing.

The scheduled workflow runs:
- `pytest`
- `rm -f data/jobs.sqlite data/jobs.sqlite-shm data/jobs.sqlite-wal` (clears test/runtime DB state before real run)
- `python -m job_fit_agent.main run`
- `python -m job_fit_agent.main rescore`
- `python -m job_fit_agent.main digest`
- `python -m job_fit_agent.main prep-next-application --skip-browser --notify-telegram`
- Uploads generated `applications/` package as a GitHub Actions artifact (`job-fit-application-package-<run_id>`, retained 14 days)

In GitHub Actions, scheduled prep installs `pandoc` plus LaTeX dependencies (`texlive-latex-base`, `texlive-latex-recommended`, `texlive-fonts-recommended`, `lmodern`) and attempts resume PDF export. `setspace.sty` is provided by `texlive-latex-recommended`. If PDF export fails, the workflow continues, `submit_resume.md` remains the manual fallback, Telegram still sends, and markdown files are still uploaded in artifacts.

Download package artifacts from: **GitHub → Actions → Job Fit Agent → latest run → Artifacts**.
Telegram package summaries now include a direct **GitHub Actions run link** when running in Actions, so you can tap from Telegram, open **Artifacts**, and download the package quickly.
Telegram now also sends the generated application package `.zip` directly as a document attachment for mobile-friendly download.
Mobile flow: **Telegram → download zip → review files → submit manually**.
GitHub Actions artifact upload remains in place as backup storage.
Local computer does not need to be on for scheduled runs, and final application submission remains manual.

Actionable recommendations in digest, prep-next-application, and Telegram notifications automatically exclude placeholder/test URLs (for example `example.com`, `localhost`, `127.0.0.1`, `test.com`, missing scheme URLs, and URLs containing `fake`/`placeholder`).

Required GitHub secrets for Telegram package summaries:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Docker/VPS remains the future multi-client runtime path.
