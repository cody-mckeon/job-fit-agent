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

> Note: Greenhouse board tokens may not match human-readable company names. For example, a company brand and the token segment in `.../boards/{token}/jobs` can differ.

To validate a token, open:

- `https://boards-api.greenhouse.io/v1/boards/{token}/jobs`

A valid token returns HTTP 200 with a `jobs` payload; an invalid token usually returns HTTP 404.

## Location fit logic (Cody profile)

Jobs are considered location-fit only when one of the following is true:

- The posting is clearly **Remote US** (`Remote US`, `Remote United States`, `US Remote`, `United States Remote`, or equivalent wording in location/description text).
- The role is in **Las Vegas**, **Henderson**, or **Nevada**.
- The role is clearly **hybrid in Las Vegas/Nevada**.

The scorer applies red flags for:

- Onsite or location-specific US cities outside Las Vegas/Nevada (for example New York or Pittsburgh), unless the posting explicitly says Remote US.
- International excluded locations (for example London, Singapore, Toronto), which receive a stronger penalty.

Title/keyword relevance alone cannot push a non-location-fit role above the default threshold.

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
