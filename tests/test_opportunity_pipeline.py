import json
import sqlite3

from job_fit_agent.main import block_company, main
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.opportunity_pipeline import build_opportunity_pipeline, pipeline_review
from job_fit_agent.repository import DB_PATH, get_job_by_url, initialize, upsert_job
from job_fit_agent.scoring import score_job
from job_fit_agent.config import load_target_profile


def _insert_job(
    company: str,
    title: str,
    *,
    score: int = 88,
    classification: str = "high_fit",
    viability: str = "apply_now",
    geo: str = "eligible",
    reasons: list[str] | None = None,
    red_flags: list[str] | None = None,
    source: str = "ashby",
) -> int:
    slug = title.lower().replace(" ", "-").replace("/", "-")
    url = f"https://jobs.ashbyhq.com/{company.lower()}/{slug}"
    job = JobPosting(
        source=source,
        company=company,
        title=title,
        location="Remote US",
        location_raw="Remote US",
        geographic_eligibility=geo,
        url=url,
        description="AI agents workflow automation product systems",
    )
    fit = FitScore(
        total_score=score,
        classification=classification,
        role_family="ai_product",
        viability_score=score,
        viability_level=viability,
        reasons=reasons or ["AI workflow automation and product systems fit"],
        red_flags=red_flags or [],
    )
    upsert_job(job, fit)
    row = get_job_by_url(url)
    assert row is not None
    return int(row["id"])


def test_ashby_scoring_still_works_independently() -> None:
    job = JobPosting(
        source="ashby",
        company="linear",
        title="AI Product Operations Manager",
        location="Remote US",
        description="Deploy AI agents, workflow automation, product analytics, and internal AI operations.",
        url="https://jobs.ashbyhq.com/linear/ai-product-ops",
    )

    fit = score_job(job, load_target_profile())

    assert fit.total_score > 0
    assert fit.classification in {"high_fit", "near_fit", "low_fit"}


def test_opportunity_pipeline_consumes_scored_jobs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("Linear", "AI Product Operations Manager", score=91)

    records = build_opportunity_pipeline()

    linear = next(record for record in records if record["company"] == "Linear")
    assert linear["best_job_score"] == 91
    assert linear["best_job_classification"] == "high_fit"
    assert linear["current_best_job_id"] == "ai-product-operations-manager"


def test_blocked_company_appears_in_cooldown_section(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("elevenlabs", "AI Implementation Lead", score=92)
    block_company("elevenlabs", "90-day Ashby cooldown", expires_at="2026-09-02", quiet=True)

    main(["opportunity-pipeline"])

    output = capsys.readouterr().out
    assert "Blocked / cooldown" in output
    assert "company: elevenlabs" in output
    assert "blocked_until: 2026-09-02" in output


def test_weak_stripe_near_fit_does_not_become_best_next_action(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job(
        "Stripe",
        "Program Manager",
        score=58,
        classification="near_fit",
        viability="apply_now",
        reasons=["generic program management"],
    )

    main(["pipeline-review"])

    payload = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert payload["recommended_company"] == "Stripe"
    assert payload["recommended_channel"] == "unknown"
    assert "forcing a weak near-fit application" in payload["reasoning"]
    assert "Apply to Stripe" not in payload["best_next_action_today"]


def test_linear_stretch_review_roles_are_manual_review_not_automatic_apply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("linear", "Product Manager", score=83, classification="near_fit", viability="strong_review")

    records = build_opportunity_pipeline()

    linear = next(record for record in records if record["company"] == "linear")
    assert linear["status"] == "relationship_strategy"
    assert linear["application_channel"] == "linkedin"
    assert "Manual review" in linear["next_action"]


def test_elevenlabs_blocked_role_recommends_recruiter_manual_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("elevenlabs", "AI Agents Product Lead", score=94)
    block_company("elevenlabs", "90-day application cooldown", expires_at="2026-09-02", quiet=True)

    payload = pipeline_review()

    assert payload["recommended_company"] == "elevenlabs"
    assert payload["recommended_channel"] == "recruiter"
    assert "recruiter/manual review" in payload["best_next_action_today"]
    assert "blocked companies" in payload["why_not_simply_apply"]


def test_no_strong_eligible_jobs_returns_research_instead_of_weak_application(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job(
        "Stripe",
        "Backend Infrastructure Engineer",
        score=62,
        classification="near_fit",
        viability="apply_now",
        reasons=["backend infrastructure, generic platform engineering"],
    )

    payload = pipeline_review()

    assert payload["recommended_channel"] in {"unknown", "linkedin"}
    assert any(term in payload["best_next_action_today"] for term in ("Research", "Watch", "Manual review"))
    assert any(term in payload["why_not_simply_apply"].lower() for term in ("weak", "relationship", "manual review"))


def test_pipeline_review_does_not_modify_job_scores(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("Linear", "AI Product Operations Manager", score=89)
    with sqlite3.connect(DB_PATH) as conn:
        before = conn.execute("SELECT score, classification, viability_level FROM jobs").fetchall()

    pipeline_review()

    with sqlite3.connect(DB_PATH) as conn:
        after = conn.execute("SELECT score, classification, viability_level FROM jobs").fetchall()
    assert after == before


def test_set_company_status_command_persists_next_action(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main([
        "set-company-status",
        "stripe",
        "watch",
        "Weak current fit, watch for AI operations, internal tools, or agent deployment roles",
    ])

    output = capsys.readouterr().out
    store = json.loads((tmp_path / "data/opportunity_pipeline.json").read_text())
    assert "stripe" in output
    assert store[0]["status"] == "watch"
    assert "agent deployment roles" in store[0]["next_action"]
