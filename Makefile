.PHONY: docker-build docker-digest docker-run docker-prep-next

docker-build:
	docker compose build

docker-digest:
	docker compose run --rm job-fit-agent python -m job_fit_agent.main digest

docker-run:
	docker compose run --rm job-fit-agent python -m job_fit_agent.main --help

docker-prep-next:
	docker compose run --rm job-fit-agent python -m job_fit_agent.main prep-next-application
