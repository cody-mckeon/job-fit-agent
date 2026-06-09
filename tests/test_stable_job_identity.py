import json

from job_fit_agent.main import main, mobile_command_alias_for_job
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.repository import get_job_by_id, get_job_by_url, initialize, upsert_job
from job_fit_agent.stable_identity import build_stable_job_key


def _fit() -> FitScore:
    return FitScore(total_score=91, classification="high_fit", role_family="product", viability_score=90, viability_level="apply_now")


def _insert(job: JobPosting) -> int:
    upsert_job(job, _fit())
    row = get_job_by_url(job.url)
    assert row is not None
    return int(row["id"])


def _greenhouse_job(url: str, company: str = "Stripe", title: str = "Product Marketing Manager, Growth") -> JobPosting:
    return JobPosting(
        source="greenhouse",
        company=company,
        title=title,
        location="Remote US",
        geographic_eligibility="eligible",
        url=url,
        description="Product growth marketing automation systems",
    )


def test_greenhouse_stripe_gh_jid_produces_source_native_key():
    job = {"id": 463, "source": "greenhouse", "company": "Stripe", "title": "Product Marketing Manager, Growth", "url": "https://stripe.com/jobs/search?gh_jid=7914005"}
    assert build_stable_job_key(job) == "greenhouse:stripe:7914005"


def test_greenhouse_stable_key_never_uses_sqlite_row_id():
    job = {"id": 922, "source": "greenhouse", "company": "Stripe", "title": "Product Marketing Manager, Growth", "url": "https://stripe.com/jobs/search?gh_jid=7914005"}
    assert build_stable_job_key(job) == "greenhouse:stripe:7914005"
    assert build_stable_job_key(job) != "greenhouse:stripe:922"


def test_github_actions_and_local_rows_with_different_ids_match():
    gh_actions = {"id": 922, "source": "greenhouse", "company": "Stripe", "title": "Product Marketing Manager, Growth", "url": "https://stripe.com/jobs/search?gh_jid=7914005"}
    local = dict(gh_actions, id=463)
    assert build_stable_job_key(gh_actions) == build_stable_job_key(local) == "greenhouse:stripe:7914005"


def test_telegram_applied_greenhouse_source_native_key_marks_stripe(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    stripe_id = _insert(_greenhouse_job("https://stripe.com/jobs/search?gh_jid=7914005"))
    vercel_id = _insert(_greenhouse_job("https://vercel.com/careers/customer-success?gh_jid=1234567", company="Vercel", title="Customer Success Manager, EMEA"))

    main(["telegram-command", "applied greenhouse:stripe:7914005"])

    result = json.loads(capsys.readouterr().out)
    stripe = get_job_by_id(stripe_id)
    vercel = get_job_by_id(vercel_id)
    assert result["success"] is True
    assert result["company"] == "Stripe"
    assert result["stable_job_key"] == "greenhouse:stripe:7914005"
    assert stripe["application_status"] == "applied"
    assert vercel["application_status"] == "not_applied"


def test_telegram_greenhouse_local_row_like_key_fails_safely_and_does_not_mark_vercel(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert(_greenhouse_job("https://stripe.com/jobs/search?gh_jid=7914005"))
    vercel_id = _insert(_greenhouse_job("https://vercel.com/careers/customer-success?gh_jid=922", company="Vercel", title="Customer Success Manager, EMEA"))

    main(["telegram-command", "applied greenhouse:stripe:922"])

    result = json.loads(capsys.readouterr().out)
    vercel = get_job_by_id(vercel_id)
    assert result["success"] is False
    assert "Unstable or mismatched Greenhouse identifier" in result["message"]
    assert "greenhouse:stripe:7914005" in result["message"]
    assert vercel["application_status"] == "not_applied"
    assert not (tmp_path / "data/application_status.json").exists()


def test_application_status_migration_converts_greenhouse_key_and_merges_duplicates(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "application_status.json").write_text(json.dumps({
        "greenhouse:stripe:463": {
            "stable_job_key": "greenhouse:stripe:463",
            "source": "greenhouse",
            "company": "stripe",
            "title": "Product Marketing Manager, Growth",
            "url": "https://stripe.com/jobs/search?gh_jid=7914005",
            "application_status": "applied",
            "applied_at": "2026-06-01T00:00:00+00:00",
            "note": "old local id",
            "status_history": [{"status": "applied", "identifier_used": "greenhouse:stripe:463"}],
        },
        "greenhouse:stripe:7914005": {
            "stable_job_key": "greenhouse:stripe:7914005",
            "source": "greenhouse",
            "company": "stripe",
            "title": "Product Marketing Manager, Growth",
            "url": "https://stripe.com/jobs/search?gh_jid=7914005",
            "application_status": "applied",
            "applied_at": "2026-06-02T00:00:00+00:00",
            "note": "canonical",
        },
    }))

    main(["migrate-stable-job-keys"])

    output = json.loads(capsys.readouterr().out)
    store = json.loads((data / "application_status.json").read_text())
    assert output["migrated"] == 1
    assert output["duplicates_merged"] == 1
    assert (data / "application_status.json.bak").exists()
    assert list(store) == ["greenhouse:stripe:7914005"]
    assert store["greenhouse:stripe:7914005"]["external_job_id"] == "7914005"
    assert "old local id" in store["greenhouse:stripe:7914005"]["note"]


def test_applied_report_displays_one_canonical_record_after_migration(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "application_status.json").write_text(json.dumps({
        "greenhouse:stripe:463": {"stable_job_key": "greenhouse:stripe:463", "source": "greenhouse", "company": "stripe", "title": "PMM", "url": "https://stripe.com/jobs/search?gh_jid=7914005", "application_status": "applied", "applied_at": "2026-06-01T00:00:00+00:00"},
        "greenhouse:stripe:7914005": {"stable_job_key": "greenhouse:stripe:7914005", "source": "greenhouse", "company": "stripe", "title": "PMM", "url": "https://stripe.com/jobs/search?gh_jid=7914005", "application_status": "applied", "applied_at": "2026-06-02T00:00:00+00:00"},
    }))
    main(["migrate-stable-job-keys"])
    capsys.readouterr()

    main(["applied"])

    output = capsys.readouterr().out
    assert output.count("stable_job_key: greenhouse:stripe:7914005") == 1
    assert "greenhouse:stripe:463" not in output


def test_mobile_command_alias_still_resolves(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert(_greenhouse_job("https://stripe.com/jobs/search?gh_jid=7914005"))
    row = dict(get_job_by_url("https://stripe.com/jobs/search?gh_jid=7914005"))
    alias = mobile_command_alias_for_job(row)

    main(["telegram-command", f"save {alias}"])

    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert result["stable_job_key"] == "greenhouse:stripe:7914005"


def test_ashby_uuid_keys_still_work():
    job = {"id": 1, "source": "ashby", "company": "elevenlabs", "url": "https://jobs.ashbyhq.com/elevenlabs/275f43d0-b62d-401d-830c-7c1ac0e688aa", "title": "PM"}
    assert build_stable_job_key(job) == "ashby:elevenlabs:275f43d0-b62d-401d-830c-7c1ac0e688aa"


def test_lever_keys_still_work():
    job = {"id": 1, "source": "lever", "company": "acme", "url": "https://jobs.lever.co/acme/product-manager", "title": "PM"}
    assert build_stable_job_key(job) == "lever:acme:product-manager"


def test_unknown_custom_job_uses_deterministic_hash_not_sqlite_id():
    first = {"id": 1, "source": "custom", "company": "Acme", "title": "Automation PM", "url": "https://example.com/jobs/automation-pm"}
    second = dict(first, id=999)
    assert build_stable_job_key(first) == build_stable_job_key(second)
    assert build_stable_job_key(first).startswith("job:acme:")
    assert not build_stable_job_key(first).endswith(":1")


def test_debug_job_identity_for_greenhouse_url_and_unstable_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert(_greenhouse_job("https://stripe.com/jobs/search?gh_jid=7914005"))

    main(["debug-job-identity", "https://stripe.com/jobs/search?gh_jid=7914005"])
    url_result = json.loads(capsys.readouterr().out)
    main(["debug-job-identity", "greenhouse:stripe:922"])
    key_result = json.loads(capsys.readouterr().out)

    assert url_result["canonical_stable_job_key"] == "greenhouse:stripe:7914005"
    assert key_result["would_accept_telegram_command"] is False
    assert any("local row id" in warning for warning in key_result["warnings"])
