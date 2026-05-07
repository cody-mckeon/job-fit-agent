# job-fit-agent

`job-fit-agent` is an agentic job search assistant focused on collecting roles from company career pages, normalizing job data, and preparing listings for downstream fit scoring.

## Supported sources

- Greenhouse boards API
  - `https://boards-api.greenhouse.io/v1/boards/{company}/jobs`

## How to run

```bash
python -m job_fit_agent.main
```

## Run tests

```bash
python -m pytest
```

## Example output

```text
Software Engineer | openai | San Francisco, CA | https://boards.greenhouse.io/openai/jobs/12345
Research Scientist | anthropic | Remote | https://boards.greenhouse.io/anthropic/jobs/67890
```
