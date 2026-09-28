from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Iterable, Sequence
import time


class AutomationLevel(str, Enum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    SEMI_AUTOMATED = "semi_automated"


@dataclass(frozen=True)
class Guardrails:
    approved_sources: frozenset[str]
    automation_level: AutomationLevel = AutomationLevel.ASSISTED
    max_applications_per_day: int = 10
    require_terms_compliance: bool = True

    def validate_source(self, source: str) -> bool:
        return source.lower() in self.approved_sources


@dataclass(frozen=True)
class ResumeVariant:
    name: str
    file_path: str
    focus: str


@dataclass(frozen=True)
class CandidateProfile:
    full_name: str
    email: str
    phone: str
    location: str
    skills: frozenset[str]
    preferred_locations: tuple[str, ...]
    salary_min: int
    salary_max: int
    visa_status: str
    work_authorization: str
    resume_variants: tuple[ResumeVariant, ...]


@dataclass(frozen=True)
class JobListing:
    source: str
    source_id: str
    company: str
    title: str
    location: str
    min_salary: int | None
    max_salary: int | None
    required_skills: frozenset[str]
    seniority: str
    url: str


@dataclass(frozen=True)
class NormalizedJob:
    uid: str
    source: str
    company: str
    title: str
    location: str
    min_salary: int | None
    max_salary: int | None
    required_skills: frozenset[str]
    seniority: str
    url: str


@dataclass(frozen=True)
class MatchResult:
    job: NormalizedJob
    score: float
    score_breakdown: dict[str, float]


class ApplicationStatus(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"


@dataclass
class ApplicationRecord:
    job_uid: str
    status: ApplicationStatus
    source: str
    applied_at: datetime | None = None
    reminder_at: datetime | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuditEntry:
    timestamp: datetime
    event: str
    details: str


class AuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def add(self, event: str, details: str) -> None:
        self._entries.append(AuditEntry(datetime.now(timezone.utc), event, details))

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: int, *, time_source: Callable[[], float] | None = None) -> None:
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._calls: list[float] = []
        self._time = time_source or time.time

    def allow(self) -> bool:
        now = self._time()
        cutoff = now - self.period_seconds
        self._calls = [t for t in self._calls if t >= cutoff]
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(now)
        return True


class RetryPolicy:
    def __init__(self, attempts: int = 3, delay_seconds: float = 0.2) -> None:
        self.attempts = attempts
        self.delay_seconds = delay_seconds

    def run(self, action: Callable[[], object]) -> object:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                return action()
            except Exception as error:  # pragma: no cover - defensive handling
                last_error = error
                if attempt < self.attempts:
                    time.sleep(self.delay_seconds)
        if last_error is not None:
            raise last_error
        raise RuntimeError("retry policy failed without an error")


class JobDiscoveryService:
    def __init__(self, guardrails: Guardrails, audit_log: AuditLog) -> None:
        self.guardrails = guardrails
        self.audit_log = audit_log

    def discover(self, jobs: Iterable[JobListing]) -> list[NormalizedJob]:
        normalized_jobs: list[NormalizedJob] = []
        seen: set[tuple[str, str, str]] = set()

        for job in jobs:
            if not self.guardrails.validate_source(job.source):
                self.audit_log.add("source_skipped", f"{job.source} is not approved")
                continue

            uid = f"{job.source.lower()}::{job.source_id.strip()}"
            dedupe_key = (
                job.company.strip().lower(),
                job.title.strip().lower(),
                job.location.strip().lower(),
            )
            if dedupe_key in seen:
                self.audit_log.add("job_deduplicated", uid)
                continue

            seen.add(dedupe_key)
            normalized_jobs.append(
                NormalizedJob(
                    uid=uid,
                    source=job.source.lower(),
                    company=job.company.strip(),
                    title=job.title.strip(),
                    location=job.location.strip(),
                    min_salary=job.min_salary,
                    max_salary=job.max_salary,
                    required_skills=frozenset(skill.lower() for skill in job.required_skills),
                    seniority=job.seniority.strip().lower(),
                    url=job.url,
                )
            )

        self.audit_log.add("job_discovery_complete", f"{len(normalized_jobs)} jobs")
        return normalized_jobs


class JobMatcher:
    def __init__(self, profile: CandidateProfile, *, weights: dict[str, float] | None = None) -> None:
        self.profile = profile
        self.weights = weights or {
            "skills": 0.45,
            "location": 0.2,
            "salary": 0.2,
            "seniority": 0.15,
        }

    def rank(self, jobs: Sequence[NormalizedJob]) -> list[MatchResult]:
        ranked = [self._score(job) for job in jobs]
        return sorted(ranked, key=lambda result: result.score, reverse=True)

    def _score(self, job: NormalizedJob) -> MatchResult:
        skills_score = self._score_skills(job)
        location_score = self._score_location(job)
        salary_score = self._score_salary(job)
        seniority_score = self._score_seniority(job)

        breakdown = {
            "skills": skills_score,
            "location": location_score,
            "salary": salary_score,
            "seniority": seniority_score,
        }
        total = sum(self.weights[key] * breakdown[key] for key in breakdown)
        return MatchResult(job=job, score=round(total, 4), score_breakdown=breakdown)

    def _score_skills(self, job: NormalizedJob) -> float:
        if not job.required_skills:
            return 0.5
        overlap = len(job.required_skills.intersection({skill.lower() for skill in self.profile.skills}))
        return overlap / len(job.required_skills)

    def _score_location(self, job: NormalizedJob) -> float:
        preferred = {loc.lower() for loc in self.profile.preferred_locations}
        return 1.0 if job.location.lower() in preferred else 0.3

    def _score_salary(self, job: NormalizedJob) -> float:
        if job.max_salary is None and job.min_salary is None:
            return 0.5
        min_salary = job.min_salary or 0
        max_salary = job.max_salary or min_salary
        if max_salary < self.profile.salary_min:
            return 0.0
        if min_salary > self.profile.salary_max:
            return 0.4
        return 1.0

    @staticmethod
    def _score_seniority(job: NormalizedJob) -> float:
        tiers = {
            "intern": 0.3,
            "junior": 0.8,
            "mid": 1.0,
            "senior": 0.7,
            "lead": 0.5,
        }
        return tiers.get(job.seniority, 0.6)


class ContentGenerator:
    def __init__(self, profile: CandidateProfile) -> None:
        self.profile = profile

    def resume_bullets(self, job: NormalizedJob) -> list[str]:
        matched_skills = sorted(job.required_skills.intersection({skill.lower() for skill in self.profile.skills}))
        skills_text = ", ".join(matched_skills) if matched_skills else "cross-functional delivery"
        return [
            f"Delivered measurable outcomes in roles aligned with {job.title} responsibilities.",
            f"Applied strengths in {skills_text} to support business-critical initiatives.",
            f"Collaborated across teams to ship reliable features for {job.company} use cases.",
        ]

    def cover_letter(self, job: NormalizedJob) -> str:
        return (
            f"Dear Hiring Team at {job.company},\n\n"
            f"I am excited to apply for the {job.title} role in {job.location}. "
            f"My background, including strengths in {', '.join(sorted(self.profile.skills))}, "
            "aligns well with your requirements.\n\n"
            "I would welcome the opportunity to contribute and discuss how my experience can support your team.\n\n"
            f"Sincerely,\n{self.profile.full_name}"
        )


class ApplicationWorkflow:
    def __init__(
        self,
        guardrails: Guardrails,
        audit_log: AuditLog,
        retry_policy: RetryPolicy,
        rate_limiter: RateLimiter,
    ) -> None:
        self.guardrails = guardrails
        self.audit_log = audit_log
        self.retry_policy = retry_policy
        self.rate_limiter = rate_limiter
        self._applied_today = 0

    def apply(
        self,
        job: NormalizedJob,
        *,
        resume_path: str,
        cover_letter: str,
        approved_by_user: bool,
        captcha_present: bool,
        submit: Callable[[NormalizedJob, str, str], str],
    ) -> tuple[bool, str]:
        if not approved_by_user:
            self.audit_log.add("application_blocked", f"{job.uid}: missing approval")
            return False, "User approval required before submit"

        if captcha_present:
            self.audit_log.add("application_manual_fallback", f"{job.uid}: captcha encountered")
            return False, "Manual fallback required due to CAPTCHA"

        if self._applied_today >= self.guardrails.max_applications_per_day:
            self.audit_log.add("application_rate_limited_daily", job.uid)
            return False, "Daily application limit reached"

        if not self.rate_limiter.allow():
            self.audit_log.add("application_rate_limited_window", job.uid)
            return False, "Rate limited: retry later"

        def action() -> str:
            return submit(job, resume_path, cover_letter)

        result = self.retry_policy.run(action)
        self._applied_today += 1
        self.audit_log.add("application_submitted", f"{job.uid}: {result}")
        return True, str(result)


class TrackingDashboard:
    def __init__(self) -> None:
        self._records: dict[str, ApplicationRecord] = {}

    def save_job(self, job_uid: str, source: str) -> None:
        self._records[job_uid] = ApplicationRecord(job_uid=job_uid, status=ApplicationStatus.SAVED, source=source)

    def mark_applied(self, job_uid: str) -> None:
        record = self._records[job_uid]
        record.status = ApplicationStatus.APPLIED
        record.applied_at = datetime.now(timezone.utc)

    def update_status(self, job_uid: str, status: ApplicationStatus) -> None:
        self._records[job_uid].status = status

    def add_note(self, job_uid: str, note: str) -> None:
        self._records[job_uid].notes.append(note)

    def set_follow_up(self, job_uid: str, days: int = 7) -> None:
        self._records[job_uid].reminder_at = datetime.now(timezone.utc) + timedelta(days=days)

    def records(self) -> tuple[ApplicationRecord, ...]:
        return tuple(self._records.values())


class AnalyticsService:
    @staticmethod
    def summarize(records: Sequence[ApplicationRecord]) -> dict[str, object]:
        total_applied = sum(1 for record in records if record.status in {
            ApplicationStatus.APPLIED,
            ApplicationStatus.INTERVIEW,
            ApplicationStatus.REJECTED,
        })
        interviews = sum(1 for record in records if record.status == ApplicationStatus.INTERVIEW)
        responses = interviews + sum(1 for record in records if record.status == ApplicationStatus.REJECTED)

        by_source: dict[str, dict[str, int]] = defaultdict(lambda: {"applied": 0, "interviews": 0})
        for record in records:
            source_summary = by_source[record.source]
            if record.status in {ApplicationStatus.APPLIED, ApplicationStatus.INTERVIEW, ApplicationStatus.REJECTED}:
                source_summary["applied"] += 1
            if record.status == ApplicationStatus.INTERVIEW:
                source_summary["interviews"] += 1

        response_rate = (responses / total_applied) if total_applied else 0.0
        interview_rate = (interviews / total_applied) if total_applied else 0.0

        best_sources = sorted(
            by_source.items(),
            key=lambda item: (item[1]["interviews"] / item[1]["applied"]) if item[1]["applied"] else 0.0,
            reverse=True,
        )

        suggestions: list[str] = []
        if interview_rate < 0.15 and total_applied >= 5:
            suggestions.append("Refine matching threshold and tailor content more aggressively.")
        if response_rate < 0.3 and total_applied >= 5:
            suggestions.append("Prioritize sources with higher historical interview rates.")
        if not suggestions:
            suggestions.append("Keep the current strategy and continue tracking outcomes.")

        return {
            "total_applied": total_applied,
            "responses": responses,
            "response_rate": round(response_rate, 4),
            "interview_rate": round(interview_rate, 4),
            "best_sources": [
                {
                    "source": source,
                    "applied": stats["applied"],
                    "interviews": stats["interviews"],
                    "interview_rate": round((stats["interviews"] / stats["applied"]) if stats["applied"] else 0.0, 4),
                }
                for source, stats in best_sources
            ],
            "suggestions": suggestions,
        }


@dataclass
class PhaseResult:
    jobs_considered: int
    jobs_ranked: int
    applications_attempted: int
    applications_submitted: int
    mode: AutomationLevel


class AutomationEngine:
    def __init__(self, profile: CandidateProfile, guardrails: Guardrails) -> None:
        self.profile = profile
        self.guardrails = guardrails
        self.audit_log = AuditLog()
        self.discovery = JobDiscoveryService(guardrails, self.audit_log)
        self.matcher = JobMatcher(profile)
        self.content = ContentGenerator(profile)
        self.tracker = TrackingDashboard()
        self.workflow = ApplicationWorkflow(
            guardrails,
            self.audit_log,
            retry_policy=RetryPolicy(),
            rate_limiter=RateLimiter(max_calls=5, period_seconds=60),
        )

    def run_phase(
        self,
        jobs: Iterable[JobListing],
        *,
        min_score: float = 0.55,
        user_approval: Callable[[MatchResult], bool],
        submit: Callable[[NormalizedJob, str, str], str],
        enable_semi_automation: bool = False,
    ) -> PhaseResult:
        normalized = self.discovery.discover(jobs)
        ranked = self.matcher.rank(normalized)
        shortlisted = [result for result in ranked if result.score >= min_score]

        submitted = 0
        for result in shortlisted:
            job = result.job
            self.tracker.save_job(job.uid, job.source)

            if self.guardrails.automation_level == AutomationLevel.MANUAL:
                self.audit_log.add("manual_queue", job.uid)
                continue

            approved = user_approval(result)
            if not approved:
                self.audit_log.add("user_rejected", job.uid)
                continue

            resume = self._select_resume(job)
            cover_letter = self.content.cover_letter(job)

            auto_submit = (
                self.guardrails.automation_level == AutomationLevel.SEMI_AUTOMATED
                and enable_semi_automation
                and approved
            )

            if not auto_submit and self.guardrails.automation_level == AutomationLevel.ASSISTED:
                self.audit_log.add("assisted_ready", job.uid)

            success, message = self.workflow.apply(
                job,
                resume_path=resume.file_path,
                cover_letter=cover_letter,
                approved_by_user=approved,
                captcha_present=False,
                submit=submit,
            )
            self.tracker.add_note(job.uid, message)
            if success:
                self.tracker.mark_applied(job.uid)
                self.tracker.set_follow_up(job.uid)
                submitted += 1

        return PhaseResult(
            jobs_considered=len(normalized),
            jobs_ranked=len(ranked),
            applications_attempted=len(shortlisted),
            applications_submitted=submitted,
            mode=self.guardrails.automation_level,
        )

    def analytics(self) -> dict[str, object]:
        return AnalyticsService.summarize(self.tracker.records())

    def _select_resume(self, job: NormalizedJob) -> ResumeVariant:
        required = job.required_skills
        variants = list(self.profile.resume_variants)
        variants.sort(
            key=lambda variant: sum(1 for token in variant.focus.lower().split() if token in required),
            reverse=True,
        )
        return variants[0]
