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

The CLI runs only enabled collectors, prints source-specific successful/failed companies, and aggregates jobs into a shared scoring pipeline.

Current runtime flag in `AppConfig`:

```python
enable_lever = False
```

## Test

```bash
python -m pytest
```
