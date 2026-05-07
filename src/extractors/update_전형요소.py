"""Batch-update admission_process.attributes["전형요소"] from 전형방법 data.

Sources (in priority order):
  1. attributes["전형방법"] — structured (from extract_admission_batch.py)
  2. Content header line "전형방법: ..." — semi-structured

After parsing, propagates parsed results to all processes with the same
(university, process_name) that have no 전형요소 yet.

Usage:
    python update_전형요소.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.parse_전형방법 import extract_전형요소_from_content, parse_전형요소

DB_PATH = Path("data/admission.db")


def main(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ── Phase 1: collect source records ──────────────────────────────────────
    rows = conn.execute(
        """
        SELECT p.id, p.process_name, p.attributes, p.content, d.university
        FROM admission_process p
        JOIN admission_department d ON d.id = p.department_id
        """
    ).fetchall()

    # (university, process_name) → parsed 전형요소 dict (best parse so far)
    uni_proc_map: dict[tuple[str, str], dict] = {}

    updates: list[tuple[str, int]] = []  # (new_attributes_json, process_id)

    for row in rows:
        pid = row["id"]
        proc_name = row["process_name"]
        university = row["university"]
        attrs: dict = json.loads(row["attributes"] or "{}")
        content = row["content"] or ""

        # Skip if already populated
        if attrs.get("전형요소"):
            continue

        # Source 1: attributes["전형방법"]
        raw = attrs.get("전형방법")
        parsed = None
        if raw:
            parsed = parse_전형요소(raw)

        # Source 2: content header
        if not parsed:
            raw_from_content = extract_전형요소_from_content(content)
            if raw_from_content:
                parsed = parse_전형요소(raw_from_content)

        if parsed:
            key = (university, proc_name)
            # Keep the most informative parse (prefer multi-stage)
            existing = uni_proc_map.get(key)
            if existing is None or len(str(parsed)) > len(str(existing)):
                uni_proc_map[key] = parsed

            attrs["전형요소"] = parsed
            updates.append((json.dumps(attrs, ensure_ascii=False), pid))

    print(f"Phase 1: {len(updates)} processes with direct parse")
    print(f"         {len(uni_proc_map)} unique (uni, process_name) pairs")

    # ── Phase 2: propagate to remaining processes in same (uni, proc_name) ───
    prop_updates: list[tuple[str, int]] = []

    if uni_proc_map:
        rows2 = conn.execute(
            """
            SELECT p.id, p.process_name, p.attributes, d.university
            FROM admission_process p
            JOIN admission_department d ON d.id = p.department_id
            WHERE json_extract(p.attributes, '$.전형요소') IS NULL
            """
        ).fetchall()

        for row in rows2:
            key = (row["university"], row["process_name"])
            parsed = uni_proc_map.get(key)
            if not parsed:
                continue
            attrs = json.loads(row["attributes"] or "{}")
            attrs["전형요소"] = parsed
            prop_updates.append((json.dumps(attrs, ensure_ascii=False), row["id"]))

    print(f"Phase 2: {len(prop_updates)} processes via propagation")
    total = len(updates) + len(prop_updates)
    print(f"Total  : {total} processes to update")

    if dry_run:
        print("[dry-run] No changes written.")
        # Print sample
        for new_attrs_json, pid in (updates + prop_updates)[:5]:
            attrs = json.loads(new_attrs_json)
            print(f"  process_id={pid}: 전형요소={attrs.get('전형요소')}")
        return

    # ── Write ─────────────────────────────────────────────────────────────────
    conn.execute("BEGIN")
    for new_attrs_json, pid in updates + prop_updates:
        conn.execute(
            "UPDATE admission_process SET attributes=? WHERE id=?",
            (new_attrs_json, pid),
        )
    conn.commit()
    print("Done.")

    # Summary of parsed 전형요소 types
    samples = conn.execute(
        """
        SELECT json_extract(attributes, '$.전형요소') as te
        FROM admission_process
        WHERE json_extract(attributes, '$.전형요소') IS NOT NULL
        LIMIT 100
        """
    ).fetchall()
    from collections import Counter
    types: Counter = Counter()
    for s in samples:
        te = json.loads(s[0])
        if "1단계" in te:
            types["multi-stage"] += 1
        else:
            keys = frozenset(k for k in te if k != "배수")
            types[str(sorted(keys))] += 1
    print("\n전형요소 type distribution (sample of 100):")
    for typ, cnt in types.most_common():
        print(f"  {typ}: {cnt}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
