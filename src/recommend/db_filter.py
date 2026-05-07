"""Stage 2: Filter candidates from DB based on StudentProfile. Pure Python/SQL, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

from ..storage.admission_store import AdmissionStore
from .models import CandidateProcess, StudentProfile

# Load university region metadata once at module level
_META_PATH = Path(__file__).parent.parent.parent / "data" / "university_meta.json"
_UNI_META: dict = {}
if _META_PATH.exists():
    with open(_META_PATH, encoding="utf-8") as _f:
        _UNI_META = json.load(_f)


def _in_region(uni: str, regions: list[str]) -> bool:
    meta = _UNI_META.get(uni, {})
    return any(
        r in (meta.get("region", ""), meta.get("region_broad", ""))
        for r in regions
    )


def _compact_content(content: str | None, max_chars: int = 300) -> str:
    """Extract a compact summary from process content."""
    if not content:
        return ""
    # Split at raw text marker if present
    parts = content.split("--- 원문")
    header = parts[0].strip()
    if len(header) > max_chars:
        header = header[:max_chars] + "..."
    return header


def filter_candidates(
    store: AdmissionStore,
    profile: StudentProfile,
    admission_type: str | None = None,
) -> list[CandidateProcess]:
    """Find candidate processes matching the student profile."""
    # Collect department IDs matching any keyword
    dept_ids: dict[int, dict] = {}
    for keyword in profile.target_department_keywords:
        depts = store.find_departments(name=keyword)
        for d in depts:
            dept_ids[d["id"]] = d

    if not dept_ids:
        return []

    # Determine admission type filter
    adm_type = admission_type or profile.preferred_admission_type

    # Collect processes for matched departments
    candidates: list[CandidateProcess] = []
    seen_ids: set[int] = set()

    for dept_id, dept in dept_ids.items():
        processes = store.find_processes(department_id=dept_id, limit=100)
        for p in processes:
            if p["id"] in seen_ids:
                continue
            seen_ids.add(p["id"])

            # Filter by admission type if specified
            if adm_type and p.get("admission_type") and p["admission_type"] != adm_type:
                continue

            # Filter by preferred process types if specified
            if profile.preferred_process_types:
                if p.get("process_type") and p["process_type"] not in profile.preferred_process_types:
                    # Allow 기타 through since it covers many valid types
                    if p["process_type"] != "기타":
                        continue

            candidates.append(CandidateProcess(
                process_id=p["id"],
                university=p["university"],
                department_name=p["department_name"],
                process_name=p["process_name"],
                process_type=p.get("process_type"),
                admission_type=p.get("admission_type"),
                quota=p.get("quota"),
                content_summary=_compact_content(p.get("content")),
            ))

    # Apply region filter from profile
    if profile.preferred_regions:
        candidates = [
            c for c in candidates
            if _in_region(c.university, profile.preferred_regions)
        ]

    # Cap at 500 to avoid overwhelming the LLM
    if len(candidates) > 500:
        # Prioritize by quota (larger programs = easier admission)
        candidates.sort(key=lambda c: -(c.quota or 0))
        candidates = candidates[:500]

    return candidates
