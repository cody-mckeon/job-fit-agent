# job-fit-agent

`job-fit-agent` collects roles from company boards, normalizes job data, and feeds them into fit scoring.

## Supported sources

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{company}/jobs`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{company}`
- Lever: integration exists in the codebase, but it is currently disabled due to invalid board tokens and low signal quality.

## Company watchlist

Configured in `config/company_watchlist.yaml`:

```yaml
greenhouse:
  - stripe

ashby:
  - anthropic
```

## Run

```bash
python -m job_fit_agent.main
```

Learn a company from a manually found job URL:

```bash
python -m job_fit_agent.main learn-url "https://jobs.ashbyhq.com/scrunch/abc123"
```

This command parses the source/company from the URL, fetches that company board, scores all jobs, persists them to SQLite, and adds the company to `config/discovery_queue.yaml`.
`discovery_queue.yaml` is for discovered companies not yet promoted to the permanent `config/company_watchlist.yaml`.

Promote a discovered company to the daily monitored watchlist:

```bash
python -m job_fit_agent.main promote-discovery ashby scrunch
```

Suggested workflow:
1. `learn-url` to ingest and score a newly found company board.
2. `digest` to review scored jobs.
3. `promote-discovery` to move the company from discovery queue into permanent watchlist monitoring.

The CLI runs only enabled collectors, prints source-specific successful/failed companies, and aggregates jobs into a shared scoring pipeline.

Current runtime flag in `AppConfig`:

```python
enable_lever = False
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

Unknown-source discoveries stay in review (approved status only) until their source is known.

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
- `recruiter_note.md`
- `application_questions.md`
- `risk_flags.md`

Export an upload-ready resume PDF for a prepared application package:

```bash
python -m job_fit_agent.main export-resume-pdf <job_id>
```

Generated filename format:
- `Cody_McKeon_<Company>_<Role>_Resume.pdf`
- Example: `Cody_McKeon_Linear_Product_Manager_Resume.pdf`

Compatibility note: if `resume_draft.md` is missing but `tailored_resume_draft.md` exists, the exporter uses the legacy file and prints a regeneration reminder.

Recommended application prep workflow:
1. `digest`
2. `set-status <job_id> interested`
3. `prep-application <job_id>`
4. Review/edit package artifacts
5. Apply manually

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
