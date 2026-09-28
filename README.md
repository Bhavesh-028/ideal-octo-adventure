# Job Application Automation Framework

This repository now includes a Python implementation for automating job application workflows with strict guardrails.

## What is implemented

1. **Scope and guardrails**
   - Approved source allowlist
   - Automation mode (`manual`, `assisted`, `semi_automated`)
   - Daily and short-window anti-spam rate limits
   - Compliance-friendly source filtering and auditing

2. **Candidate profile capture**
   - Resume variants
   - Skills and preferred locations
   - Salary range
   - Visa/work authorization status

3. **Job discovery pipeline**
   - Normalize incoming listings
   - Filter unapproved sources
   - Deduplicate similar listings

4. **Matching and ranking**
   - Weighted scoring by skills, location, salary, and seniority
   - Ranked shortlist generation

5. **Application workflow**
   - Explicit user approval gate before submission
   - Resume/cover letter payload support
   - Retry and rate-limit protections
   - CAPTCHA/manual fallback path

6. **Tailored content generation**
   - Role-specific resume bullet generation
   - Cover letter generation from profile and role data

7. **Tracking dashboard model**
   - Status lifecycle: saved/applied/interview/rejected
   - Follow-up reminders
   - Notes and timeline fields

8. **Reliability and safety controls**
   - Audit logs for all critical events
   - Retries for transient failures
   - Request throttling and daily limits

9. **Analytics**
   - Application-to-response and interview rates
   - Source-level performance
   - Strategy suggestions

10. **Phased rollout support**
    - MVP mode with assisted flow
    - Optional semi-automation gate

## Main file

- `/home/runner/work/ideal-octo-adventure/ideal-octo-adventure/job_automation.py`

## Minimal usage example

```python
from job_automation import (
    AutomationEngine,
    AutomationLevel,
    CandidateProfile,
    Guardrails,
    JobListing,
    ResumeVariant,
)

profile = CandidateProfile(
    full_name="A Candidate",
    email="a@example.com",
    phone="+1-555-0101",
    location="Bengaluru",
    skills=frozenset({"python", "sql", "automation"}),
    preferred_locations=("Bengaluru", "Remote"),
    salary_min=1200000,
    salary_max=2500000,
    visa_status="N/A",
    work_authorization="Authorized",
    resume_variants=(
        ResumeVariant(name="General", file_path="./resume-general.pdf", focus="python automation"),
    ),
)

guardrails = Guardrails(
    approved_sources=frozenset({"linkedin", "wellfound", "greenhouse"}),
    automation_level=AutomationLevel.ASSISTED,
    max_applications_per_day=10,
)

engine = AutomationEngine(profile, guardrails)

jobs = [
    JobListing(
        source="linkedin",
        source_id="123",
        company="Example Inc",
        title="Python Developer",
        location="Remote",
        min_salary=1500000,
        max_salary=2200000,
        required_skills=frozenset({"python", "sql"}),
        seniority="mid",
        url="https://example.com/jobs/123",
    )
]

result = engine.run_phase(
    jobs,
    user_approval=lambda match: match.score >= 0.6,
    submit=lambda job, resume, cover: f"submitted:{job.uid}",
    enable_semi_automation=False,
)

analytics = engine.analytics()
```

## Notes

- The framework is intentionally source-agnostic: collectors and form fillers can be integrated later using approved APIs.
- User approval remains mandatory before submission to keep control and reduce policy/compliance risk.
