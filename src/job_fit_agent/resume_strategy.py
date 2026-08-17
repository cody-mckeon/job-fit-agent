"""Deterministic role-lane classification and resume tailoring guidance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResumeStrategy:
    lane: str
    headline: str
    summary: str
    core_skills: tuple[str, ...]
    methods_title: str | None
    methods: tuple[str, ...]
    tools: tuple[str, ...]
    projects: tuple[str, ...]
    excluded_projects: tuple[str, ...]
    experience_emphasis: tuple[str, ...]
    score: int = 0
    runner_up_score: int = 0
    low_confidence: bool = False


COMMON_TOOLS = ("OpenAI API", "GPT-5.5", "Python", "GitHub / GitHub Actions", "Figma", "GA4", "Google Tag Manager", "Pendo", "OneTrust", "Asana")


def _strategy(lane: str, headline: str, summary: str, skills: tuple[str, ...], methods_title: str | None,
              methods: tuple[str, ...], projects: tuple[str, ...], emphasis: tuple[str, ...],
              tools: tuple[str, ...] = COMMON_TOOLS, excluded: tuple[str, ...] = ("Job Fit Agent",)) -> ResumeStrategy:
    return ResumeStrategy(lane, headline, summary, skills, methods_title, methods, tools, projects, excluded, emphasis)


STRATEGIES = {
    "ai_strategy_transformation": _strategy(
        "ai_strategy_transformation", "AI Transformation Leader | Enterprise AI Adoption | Workflow Automation",
        "Enterprise AI transformation leader focused on AI adoption, workflow automation, AI literacy, change enablement, stakeholder communications, scalable AI programs, and measurable productivity and operational outcomes.",
        ("Enterprise AI Strategy", "AI Transformation", "AI Adoption", "AI Literacy", "Workflow Automation", "Agentic AI Workflows", "Change Enablement", "Stakeholder Communications", "Communities of Practice", "AI Champion Programs", "Use Case Prioritization", "Program Leadership", "Responsible AI", "Prompt Strategy", "Training & Enablement", "Productivity Improvement", "Operational Performance"),
        "AI Transformation Methods", ("Enterprise AI Literacy", "AI Academy Program Design", "Champion Network Development", "Communities of Practice", "Workflow Mapping", "Use Case Intake", "AI Tool Enablement", "Prompt Design", "Responsible AI Practices", "Adoption Tracking", "Stakeholder Updates", "Executive Reporting", "Change Management", "Training Resources", "Automation Roadmaps", "Impact Measurement"),
        ("AI Marketing Intelligence Platform", "AI Product Design Operating System", "RWLV Priority Governor Agent", "Site Audit QA Agent"),
        ("enterprise adoption and enablement", "stakeholder and senior-leader communications", "change management", "measurable transformation outcomes"),
        ("OpenAI API", "GPT-5.5", "OpenClaw", "Hermes Agent", "Python", "GitHub / GitHub Actions", "Asana", "GA4", "Pendo", "Figma")),
    "data_platform_analytics_product": _strategy(
        "data_platform_analytics_product", "Senior Product Manager | Data Platforms | Customer Data & AI-Enabled Analytics",
        "Data product leader focused on data platforms, analytics instrumentation, customer data workflows, governance, event tracking, privacy and consent, and AI-enabled data products.",
        ("Data Platform Strategy", "Customer Data Platforms", "Single Customer View", "Analytics Instrumentation", "Event Taxonomy", "Data Governance", "Data Quality", "Identity Resolution", "Segmentation & Activation", "Privacy & Consent", "Product Management"),
        "Data Product Methods", ("Event Tracking", "Taxonomy Design", "Schema Planning", "Data Quality", "Observability", "Identity Resolution", "Profile Stitching", "Consent Governance", "Segmentation", "Activation"),
        ("Site Audit QA Agent", "AI Marketing Intelligence Platform", "AI Product Design Operating System", "RWLV Priority Governor Agent"),
        ("data quality and governance", "instrumentation and taxonomy", "customer-data activation", "cross-functional platform delivery"),
        ("GA4", "Google Tag Manager", "Pendo", "OneTrust", "Python", "SQLite", "OpenAI API", "GitHub / GitHub Actions")),
    "ai_systems_integration": _strategy(
        "ai_systems_integration", "AI Systems Leader | Enterprise Automation | AI Integration Strategy",
        "AI systems leader focused on enterprise automation, agentic workflows, integration planning, workflow mapping, internal tools, governance, feedback loops, and operational performance.",
        ("AI Systems Strategy", "Enterprise Automation", "Systems Integration", "Agentic Workflows", "Integration Architecture", "Workflow Mapping", "Governance Guardrails", "Technical Standards", "Vendor Partnerships", "Operational Performance"),
        "AI Operating Methods", ("AI Roadmaps", "Workflow Mapping", "Integration Planning", "Connector Design", "Governance Guardrails", "Feedback Loops", "Agent Monitoring", "Vendor Coordination", "Usage Measurement"),
        ("RWLV Priority Governor Agent", "AI Product Design Operating System", "AI Marketing Intelligence Platform", "Site Audit QA Agent"),
        ("systems integration and architecture", "automation governance", "vendor coordination", "feedback loops and operational outcomes")),
    "technical_project_program_management": _strategy(
        "technical_project_program_management", "Senior Project Manager | Digital Transformation | Cross-Functional Delivery",
        "Senior project leader focused on digital delivery, transformation programs, stakeholder alignment, requirements, release readiness, QA/UAT, vendor coordination, and executive reporting.",
        ("Program Leadership", "Digital Transformation", "Integrated Project Planning", "RAID Management", "Requirements Management", "QA & UAT", "Release Readiness", "Vendor Coordination", "Executive Reporting", "Delivery Governance"),
        "Delivery Methods", ("Integrated Project Plans", "RAID Logs", "Dependency Management", "QA Planning", "UAT Coordination", "Release Readiness", "Hypercare", "Executive Status Reporting", "Delivery Governance"),
        ("Site Audit QA Agent", "RWLV Priority Governor Agent", "AI Marketing Intelligence Platform", "AI Product Design Operating System"),
        ("roadmap and dependency management", "QA/UAT and release readiness", "vendor coordination", "executive status and delivery governance"),
        ("Asana", "GitHub / GitHub Actions", "Figma", "GA4", "Google Tag Manager", "Pendo", "OneTrust", "Python")),
    "ai_workflow_automation_solutions": _strategy(
        "ai_workflow_automation_solutions", "AI Workflow Automation Specialist | Internal Tools | AI Solutions",
        "AI solutions specialist focused on practical workflow automation, internal tools, AI-assisted operations, API-connected workflows, implementation, stakeholder intake, and measurable process improvement.",
        ("Workflow Automation", "AI Solutions", "Internal Tools", "Process Automation", "API Workflows", "Implementation", "Stakeholder Discovery", "AI-Assisted Operations"),
        "Automation Methods", ("Workflow Mapping", "Process Discovery", "Use Case Intake", "API Integration", "Rapid Prototyping", "Implementation Planning", "Impact Measurement"),
        ("Site Audit QA Agent", "AI Marketing Intelligence Platform", "RWLV Priority Governor Agent", "AI Product Design Operating System", "Job Fit Agent"),
        ("workflow implementation", "stakeholder intake", "API-connected automation", "measurable process improvement"), excluded=()),
    "product_marketing": _strategy(
        "product_marketing", "Product Marketing Manager | AI-Enabled Product Storytelling | GTM Strategy",
        "Product marketing leader focused on product narrative, launch strategy, technical fluency, AI-enabled research, enablement content, positioning, and adoption.",
        ("Product Marketing", "Positioning", "GTM Strategy", "Launch Messaging", "Sales Enablement", "Product Narrative", "AI-Enabled Research", "Adoption"),
        "Go-to-Market Methods", ("Positioning", "Messaging", "Launch Planning", "Audience Research", "Sales Enablement", "Adoption Measurement"),
        ("AI Marketing Intelligence Platform", "AI Product Design Operating System", "Site Audit QA Agent", "RWLV Priority Governor Agent"),
        ("launch execution", "positioning and narrative", "cross-functional enablement", "adoption outcomes")),
    "local_contract_consulting": _strategy(
        "local_contract_consulting", "AI Workflow Consultant | Hospitality Automation | Practical AI Implementation",
        "AI workflow consultant focused on practical AI implementation, workflow diagnostics, stakeholder discovery, automation pilots, hospitality operations, and measurable operational improvement.",
        ("AI Consulting", "Workflow Diagnostics", "Hospitality Automation", "Stakeholder Discovery", "Automation Pilots", "Process Improvement", "Practical AI Implementation"),
        "Consulting Methods", ("Discovery Workshops", "Workflow Diagnostics", "Pilot Scoping", "Implementation Planning", "Stakeholder Training", "Impact Measurement"),
        ("AI Marketing Intelligence Platform", "Site Audit QA Agent", "RWLV Priority Governor Agent", "AI Product Design Operating System"),
        ("client discovery", "practical implementation", "hospitality operations", "measurable process improvement")),
    "product_management": _strategy(
        "product_management", "Technical Product Manager | AI-Powered Digital Products | Product Analytics",
        "Technical product builder focused on AI-enabled workflow systems, internal tools, product analytics, and agentic operations.",
        ("Technical Product Management", "Product Roadmaps", "Product Discovery", "Feature Prioritization", "Product Analytics", "Requirements", "Stakeholder Alignment", "AI-Assisted Product Development"),
        "Product Methodologies", ("Product Roadmap", "Product Discovery", "Feature Prioritization", "User Behavior Analysis", "Product Requirements", "User Stories", "Backlog Prioritization", "Product Lifecycle", "Stakeholder Alignment"),
        ("AI Product Design Operating System", "RWLV Priority Governor Agent", "Job Fit Agent", "AI Marketing Intelligence Platform"),
        ("product discovery and requirements", "roadmap prioritization", "analytics-informed decisions", "cross-functional execution"),
        ("OpenClaw", "Hermes Agent", "GPT-5.5", "OpenAI API", "local LLMs", "Qwen 3", "Python", "GitHub / GitHub Actions", "SQLite", "Telegram Bot API", "Asana", "Pendo", "GA4", "Google Tag Manager", "OneTrust", "Figma", "pytest"), excluded=()),
}


KEYWORDS = {
    "ai_strategy_transformation": ("ai strategy", "ai transformation", "enterprise ai", "ai literacy", "ai academy", "ai adoption", "change enablement", "communities of practice", "champion network", "responsible ai", "workflow-integrated automation", "productivity outcomes", "senior leader updates", "stakeholder communications", "scalable ai programs"),
    "data_platform_analytics_product": ("data platform", "cdp", "single customer view", "customer data", "identity resolution", "profile stitching", "event tracking", "telemetry", "taxonomy", "schema", "data governance", "data quality", "observability", "segmentation", "activation", "snowflake", "bigquery", "kafka", "pii", "consent"),
    "ai_systems_integration": ("ai systems", "ai stack", "integration", "architecture", "technical standards", "connectors", "governance guardrails", "deployed agents", "automations", "feedback loops", "vendor partners", "token usage", "ai roadmap", "department workflows", "data intelligence"),
    "technical_project_program_management": ("project manager", "program manager", "transformation", "ecommerce transformation", "roadmap execution", "integrated project plan", "raid", "uat", "qa", "hypercare", "vendor coordination", "executive status", "delivery governance", "dependencies", "release readiness"),
    "ai_workflow_automation_solutions": ("workflow automation", "ai solutions", "automation specialist", "operations automation", "agents", "internal tools", "process automation", "api workflows", "no-code", "low-code", "implementation"),
    "product_marketing": ("product marketing", "positioning", "launch messaging", "enablement", "changelog", "go-to-market", "gtm strategy", "narrative", "sales enablement"),
    "local_contract_consulting": ("consultant", "contract", "1099", "workflow diagnostic", "local business", "hospitality operator", "automation project", "implementation partner", "process improvement"),
    "product_management": ("product manager", "product management", "product lead", "product roadmap", "product discovery", "feature prioritization", "backlog"),
}


def classify_resume_strategy(job_title: str, description: str = "", role_family: str = "") -> ResumeStrategy:
    """Score exact phrases deterministically; title matches receive a 3x tie-break weight."""
    title = job_title.casefold()
    body = f"{description} {role_family}".casefold()
    scores = {lane: sum(3 for term in terms if term in title) + sum(1 for term in terms if term in body)
              for lane, terms in KEYWORDS.items()}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], list(KEYWORDS).index(item[0])))
    lane, score = ranked[0]
    runner_up = ranked[1][1]
    low_confidence = score < 2
    if low_confidence:
        lane = "product_management"
    base = STRATEGIES[lane]
    return ResumeStrategy(**{**base.__dict__, "score": score, "runner_up_score": runner_up, "low_confidence": low_confidence})
