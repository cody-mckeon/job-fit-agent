from pathlib import Path

from job_fit_agent.main import execute_telegram_status_command, main, parse_prep_url, prep_manual_job, prep_url
from job_fit_agent.models import FitScore
from job_fit_agent.repository import get_job_by_url, initialize, update_notes

JOB_URL = "https://jobs.ashbyhq.com/elevenlabs/275f43d0-b62d-401d-830c-7c1ac0e688aa"


def _html(title: str = "AI Product Manager", description: str = "Build AI workflow automation products for internal tools and product analytics teams.") -> str:
    return f"""
    <html>
      <head><title>{title} - Ashby</title></head>
      <body>
        <main>
          <h1>{title}</h1>
          <section>
            <h4>Department</h4><p>Growth</p>
            <h4>Location</h4><p>Remote - USA</p>
            <h4>Location Type</h4><p>Remote</p>
          </section>
          <article><p>{description}</p><p>This role partners with product, engineering, and operations.</p></article>
        </main>
      </body>
    </html>
    """


def _fit(job, profile):
    job.geographic_eligibility = "eligible"
    job.location_raw = job.location
    job.normalized_country = "US"
    return FitScore(
        total_score=92,
        classification="high_fit",
        role_family="product_management",
        viability_score=90,
        viability_level="apply_now",
        reasons=["Strong AI product overlap"],
        viability_reasons=["Remote US role"],
        red_flags=[],
    )


def _setup_package_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "base_resume.md").write_text("# Cody McKeon\n\nProduct systems and AI workflow automation experience.\n", encoding="utf-8")
    (profile / "profile_context.yaml").write_text("strengths:\n  - product systems\n  - workflow automation\n", encoding="utf-8")
    (profile / "resume_rules.yaml").write_text("rules:\n  - Do not invent metrics.\n", encoding="utf-8")


def test_prep_url_parses_ashby_url() -> None:
    parsed = parse_prep_url(JOB_URL)
    assert parsed.source == "ashby"
    assert parsed.company == "elevenlabs"
    assert parsed.job_id == "275f43d0-b62d-401d-830c-7c1ac0e688aa"


def test_prep_url_inserts_missing_ashby_job(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *args, **kwargs: type("Page", (), {"html": _html(), "fetched_with_browser": False})())
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)

    summary = prep_url(JOB_URL, skip_browser=True, skip_pdf=True)

    row = get_job_by_url(JOB_URL)
    assert row is not None
    assert row["company"] == "elevenlabs"
    assert row["title"] == "AI Product Manager"
    assert row["source"] == "ashby"
    assert summary is not None
    assert summary["stable_job_key"] == "ashby:elevenlabs:275f43d0-b62d-401d-830c-7c1ac0e688aa"


def test_prep_url_updates_existing_ashby_job(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    initialize()
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *args, **kwargs: type("Page", (), {"html": _html(title="Old Title"), "fetched_with_browser": False})())
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)
    first = prep_url(JOB_URL, skip_browser=True, skip_pdf=True)
    assert first is not None
    update_notes(first["job_id"], "old description")

    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *args, **kwargs: type("Page", (), {"html": _html(title="Senior AI Product Manager", description="Updated AI workflow automation package."), "fetched_with_browser": False})())
    second = prep_url(JOB_URL, skip_browser=True, skip_pdf=True)

    row = get_job_by_url(JOB_URL)
    assert row is not None
    assert second is not None
    assert second["job_id"] == first["job_id"]
    assert row["title"] == "Senior AI Product Manager"
    assert "Updated AI workflow automation package" in row["notes"]


def test_prep_url_generates_package(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *args, **kwargs: type("Page", (), {"html": _html(), "fetched_with_browser": False})())
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)

    summary = prep_url(JOB_URL, skip_browser=True, skip_pdf=True)

    assert summary is not None
    assert Path(summary["application_folder"]).exists()
    assert (Path(summary["application_folder"]) / "fit_summary.md").exists()
    assert summary["package_zip_created"] is True
    assert Path(summary["package_zip_path"]).exists()


def test_prep_url_workday_description_file_generates_package(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    workday_url = "https://lennar.wd1.myworkdayjobs.com/Lennar_Jobs/job/Austin-TX---Virtual/Product-Manager--Digital-Buying---Selling_R26_0000002533?source=Monster.com"
    description_file = tmp_path / "workday_description.txt"
    description_file.write_text(
        "Own AI workflow automation, product analytics, digital buying experimentation, roadmap strategy, stakeholder discovery, and cross-functional delivery for customer-facing homebuying journeys.",
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *args, **kwargs: None)
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)

    summary = prep_url(workday_url, description_file=str(description_file), force=True, skip_browser=True, skip_pdf=True)

    canonical_url = "https://lennar.wd1.myworkdayjobs.com/Lennar_Jobs/job/Austin-TX---Virtual/Product-Manager--Digital-Buying---Selling_R26_0000002533"
    row = get_job_by_url(canonical_url)
    assert row is not None
    assert len(row["notes"]) > 0
    assert row["classification"] != "needs_review"
    assert summary is not None
    assert summary["stable_job_key"] == "workday:lennar:R26_0000002533"
    fit_summary = (Path(summary["application_folder"]) / "fit_summary.md").read_text(encoding="utf-8")
    resume_draft = (Path(summary["application_folder"]) / "resume_draft.md").read_text(encoding="utf-8")
    assert "workflow automation" in resume_draft.lower()
    assert "product analytics" in row["notes"].lower()


def test_prep_url_supports_force(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *args, **kwargs: type("Page", (), {"html": _html(), "fetched_with_browser": False})())
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())

    def low_fit(job, profile):
        job.geographic_eligibility = "review"
        return FitScore(total_score=20, classification="low_fit", role_family="other", viability_score=10, viability_level="skip")

    monkeypatch.setattr("job_fit_agent.main.score_job", low_fit)

    blocked = prep_url(JOB_URL, skip_browser=True, skip_pdf=True)
    forced = prep_url(JOB_URL, force=True, skip_browser=True, skip_pdf=True)

    assert blocked is None
    assert forced is not None
    assert forced["classification"] == "low_fit"
    assert "--force" in forced["warning"]


def test_prep_url_rejects_unsupported_url_with_useful_message(capsys) -> None:
    main(["prep-url", "https://jobs.lever.co/acme/abc123"])
    output = capsys.readouterr().out
    assert "Unsupported job URL for prep-url" in output
    assert "https://jobs.ashbyhq.com/<company>/<job_id>" in output


def test_prep_url_appears_in_help(capsys) -> None:
    main(["--help"])
    output = capsys.readouterr().out
    assert "python -m job_fit_agent.main prep-url <job_url> [--description-file <path>] [--force] [--skip-browser] [--skip-pdf] [--notify-telegram] [--debug]" in output


CUSTOM_URL = "https://careers.fontainebleaulasvegas.com/posting/digital-tech-product-management-director/P1-6172162-2/?keyword=product"
MANUAL_KEY = "manual:fontainebleau-las-vegas:P1-6172162-2"


def test_prep_manual_job_creates_normal_package_and_stable_key(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    description_file = tmp_path / "fontainebleau.txt"
    description_file.write_text("Lead digital product management, AI workflow automation, product analytics, and cross-functional delivery.", encoding="utf-8")
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)

    summary = prep_manual_job(company="Fontainebleau Las Vegas", title="Digital Tech Product Management Director",
        job_url=CUSTOM_URL, location="Las Vegas, NV", description_file=str(description_file),
        force=True, skip_browser=True, skip_pdf=True)

    assert summary is not None
    assert summary["stable_job_key"] == MANUAL_KEY
    assert summary["message"] == "Application Package Ready"
    assert summary["status_commands"]["applied"] == f"applied {MANUAL_KEY}"
    app_dir = Path(summary["application_folder"])
    assert (app_dir / "submit_resume.md").exists()
    assert Path(summary["package_zip_path"]).exists()
    row = get_job_by_url(CUSTOM_URL)
    assert row is not None
    assert row["source"] == "manual"
    assert "workflow automation" in row["notes"]


def test_prep_manual_job_includes_pdf_when_export_pipeline_is_available(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    description_file = tmp_path / "job.txt"
    description_file.write_text("AI workflow automation product leadership", encoding="utf-8")
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)

    def available_pdf_export(job_id: int) -> None:
        app_dir = next((tmp_path / "applications").iterdir())
        (app_dir / "Cody_McKeon_Fontainebleau_Las_Vegas_Director_Resume.pdf").write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", available_pdf_export)
    summary = prep_manual_job(company="Fontainebleau Las Vegas", title="Director", job_url=CUSTOM_URL,
        location="Las Vegas, NV", description_file=str(description_file), force=True, skip_browser=True)

    assert summary is not None
    assert summary["pdf_export"] == "generated"
    assert Path(summary["resume_pdf_path"]).exists()


def test_manual_stable_key_supports_application_status_commands(monkeypatch, tmp_path: Path) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    description_file = tmp_path / "job.txt"
    description_file.write_text("AI workflow automation product leadership", encoding="utf-8")
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)
    prep_manual_job(company="Fontainebleau Las Vegas", title="Director", job_url=CUSTOM_URL,
        location="Las Vegas, NV", description_file=str(description_file), force=True, skip_browser=True, skip_pdf=True)

    for command, expected in (("applied", "applied"), ("rejected", "rejected"), ("interviewing", "interviewing"), ("save", "saved")):
        result = execute_telegram_status_command(f"{command} {MANUAL_KEY}")
        assert result["success"] is True
        assert result["new_status"] == expected
    skipped = execute_telegram_status_command(f"skip {MANUAL_KEY} Role closed")
    assert skipped["success"] is True
    assert skipped["new_status"] == "skipped"


def test_custom_url_fails_prep_url_but_cli_accepts_manual_job(monkeypatch, tmp_path: Path, capsys) -> None:
    _setup_package_workspace(tmp_path, monkeypatch)
    description_file = tmp_path / "job.txt"
    description_file.write_text("AI workflow automation product leadership", encoding="utf-8")
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.score_job", _fit)

    main(["prep-url", CUSTOM_URL])
    assert "Unsupported job URL" in capsys.readouterr().out
    main(["prep-manual-job", "--company", "Fontainebleau Las Vegas", "--title", "Director", "--url", CUSTOM_URL,
        "--location", "Las Vegas, NV", "--description-file", str(description_file), "--force", "--skip-browser", "--skip-pdf"])
    assert "Application Package Ready" in capsys.readouterr().out
