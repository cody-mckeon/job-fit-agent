# job-fit-agent

`job-fit-agent` is an agentic job search assistant focused on helping you discover relevant roles, score role fit, save matched opportunities, and later notify you about updates.

## First milestone (this commit)

- Scaffolded Python 3.11+ project layout.
- Added core data models using Pydantic.
- Added an initial keyword-based job fit scoring engine.
- Added tests for high-fit and low-fit scenarios.

## Next milestones

1. Add job collectors (starting with Greenhouse API integration).
2. Persist normalized job postings and fit scores.
3. Add filtering and deduplication rules.
4. Add notification layer (email/Slack/SMS).
5. Add scheduling/orchestration for recurring searches.
6. Add CLI options and config management for user preferences.

## Run tests

```bash
python -m pytest
```
