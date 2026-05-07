# job-fit-agent

`job-fit-agent` is an agentic job search assistant focused on collecting roles from company career pages, normalizing job data, and preparing listings for downstream fit scoring.

## Supported sources

- Greenhouse boards API
  - `https://boards-api.greenhouse.io/v1/boards/{company}/jobs`

## Company watchlist configuration

The active company list is stored in `config/company_watchlist.yaml`.

```yaml
greenhouse:
  - stripe
  - duolingo
```

To add or remove companies, edit this list and run the pipeline again.

> Note: Greenhouse board tokens may not match human-readable company names. Use the board token that works in the API URL.

## How to run

```bash
python -m job_fit_agent.main
```

When run, the CLI prints the source and company list it is checking before job output.

## Run tests

```bash
python -m pytest
```

## Example output

```text
source: greenhouse
companies: stripe, duolingo
Software Engineer | openai | San Francisco, CA | https://boards.greenhouse.io/openai/jobs/12345
Research Scientist | anthropic | Remote | https://boards.greenhouse.io/anthropic/jobs/67890
```
