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
