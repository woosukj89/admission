"""Batch-tag admission_process.attributes["특수전형"] for restricted-access processes.

Special 전형s (기회균형, 농어촌, 특성화고, 재직자 등) are only open to students
who meet specific eligibility criteria. General students cannot apply to these.
Tagging them allows MCP tools to filter them out by default.

Usage:
    python tag_special_admissions.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/admission.db")

# Patterns in process_name that indicate restricted-access 전형s
# Each tuple: (substring_to_match, tag_label)
# Ordered from most-specific to broadest to avoid false positives
SPECIAL_PATTERNS: list[tuple[str, str]] = [
    # 사회배려 / 경제배려
    ("사회배려", "사회배려"),
    ("경제배려", "경제배려"),
    ("차상위", "저소득"),
    ("기초생활", "저소득"),
    ("저소득", "저소득"),

    # 기회균형 (일부는 일반 학생에게 열린 경우도 있으나 대부분 제한)
    ("기회균형", "기회균형"),
    ("고른기회", "기회균형"),
    ("사회통합", "사회통합"),

    # 농어촌
    ("농어촌", "농어촌"),

    # 특성화고 / 직업계고
    ("특성화고", "특성화고"),
    ("직업계고", "특성화고"),
    ("마이스터고", "특성화고"),

    # 재직자 / 성인 / 만학도
    ("재직자", "재직자"),
    ("만학도", "만학도"),
    ("성인학습자", "만학도"),
    ("자립지원", "자립지원"),

    # 장애인 / 특수교육
    ("특수교육대상자", "특수교육"),
    ("장애인", "특수교육"),

    # 다문화 / 탈북
    ("다문화", "다문화"),
    ("북한이탈", "탈북"),
    ("탈북", "탈북"),

    # 군 관련
    ("군사학", "군인"),

    # 국가보훈
    ("국가보훈", "보훈"),
    ("보훈", "보훈"),

    # 전문대학 졸업자 / 학사편입
    ("학사편입", "편입"),
    ("전문대학졸업", "편입"),

    # 정원외 특별전형 — catch-all for remaining
    # NOTE: "지역인재" is handled separately via C2 (지역인재 flag)
]

# Whitelist: patterns that look special but are NOT restricted
# e.g. "기회의균형" is not "기회균형", "농어촌테마파크학과" is not 농어촌전형
WHITELIST_PATTERNS: list[str] = [
    "기회의 균형",
    "농어촌테마",
    "농업생명",
    "농식품",
    "기초의학",
    "기초과학",
    "기초교육",
]


def _is_special(process_name: str) -> str | None:
    """Return tag label if process_name matches a special 전형 pattern, else None."""
    # Skip whitelist
    for wl in WHITELIST_PATTERNS:
        if wl in process_name:
            return None

    for pattern, label in SPECIAL_PATTERNS:
        if pattern in process_name:
            return label
    return None


def main(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, process_name, attributes FROM admission_process"
    ).fetchall()

    updates: list[tuple[str, int]] = []
    tag_counts: dict[str, int] = {}

    for row in rows:
        pid = row["id"]
        process_name = row["process_name"] or ""
        attrs: dict = json.loads(row["attributes"] or "{}")

        tag = _is_special(process_name)

        # Determine desired state
        if tag:
            # Already correctly tagged → skip
            if attrs.get("특수전형") == tag:
                continue
            attrs["특수전형"] = tag
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            updates.append((json.dumps(attrs, ensure_ascii=False), pid))
        else:
            # Remove stale tag if present
            if "특수전형" in attrs:
                del attrs["특수전형"]
                updates.append((json.dumps(attrs, ensure_ascii=False), pid))

    total_tagged = sum(tag_counts.values())
    print(f"Tagged  : {total_tagged} processes as special 전형s")
    print(f"Untagged: {len(updates) - total_tagged} stale tags removed")
    print("\nTag breakdown:")
    for tag, cnt in sorted(tag_counts.items(), key=lambda x: -x[1]):
        print(f"  {tag}: {cnt}")

    if dry_run:
        print("\n[dry-run] No changes written.")
        # Print samples
        print("\nSamples:")
        shown = 0
        for new_attrs_json, pid in updates:
            if shown >= 10:
                break
            attrs = json.loads(new_attrs_json)
            row = conn.execute(
                "SELECT process_name FROM admission_process WHERE id=?", (pid,)
            ).fetchone()
            if row:
                print(f"  id={pid} [{attrs.get('특수전형', 'REMOVE')}] {row[0]}")
            shown += 1
        return

    conn.execute("BEGIN")
    for new_attrs_json, pid in updates:
        conn.execute(
            "UPDATE admission_process SET attributes=? WHERE id=?",
            (new_attrs_json, pid),
        )
    conn.commit()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
