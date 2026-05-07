"""Download 입시결과 cut scores from adiga.kr for universities missing result data.

Flow:
1. Parse adiga_main.html → {unv_name: unv_code} mapping
2. Get CSRF token + session from adiga.kr
3. Call admssUnivAjax.do once to get all {unvCd: [comScsbjtCd...]} dept codes
4. For each missing university's departments, call admssUnivDetailLstAjax.do
5. Parse cut scores and insert into admission_result via AdmissionStore
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

BASE_URL = "https://www.adiga.kr"
MENU_ID = "PCPRCINF2000"
RESULT_YEAR = 2025
DELAY = 0.4  # seconds between detail API calls

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": BASE_URL,
}


# ── University code mapping ────────────────────────────────────────────────────

def parse_unv_codes() -> dict[str, str]:
    """Parse adiga_main.html → {university_name: unvCd}."""
    html_path = DATA_DIR / "adiga_main.html"
    html = html_path.read_text(encoding="utf-8")

    pattern = re.compile(
        r'<input[^>]*class="univGroupInput"[^>]*value="(\d+)"[^>]*/>\s*'
        r'<label[^>]*>\s*(.+?)\s*<strong',
        re.DOTALL,
    )
    codes: dict[str, str] = {}
    for m in pattern.finditer(html):
        code = m.group(1)
        raw = m.group(2).strip()
        name = re.sub(r"\[.*?\]$", "", raw).strip()
        if name not in codes:
            codes[name] = code
    return codes


# ── Session + CSRF ────────────────────────────────────────────────────────────

def get_session_and_csrf(client: httpx.Client, unv_code: str) -> str:
    """GET university page to establish session and extract CSRF token."""
    url = f"{BASE_URL}/ucp/prc/uni/admssUnivView.do"
    r = client.get(url, params={"menuId": MENU_ID, "unvCd": unv_code})
    r.raise_for_status()
    m = re.search(r'<meta name="_csrf" content="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("CSRF token not found in page response")
    return m.group(1)


# ── Dept code list ─────────────────────────────────────────────────────────────

def get_all_dept_codes(client: httpx.Client, csrf: str) -> dict[str, list[dict]]:
    """Call admssUnivAjax.do to get ALL departments across all universities.

    Returns {unvCd: [{comScsbjtCd, dept_name}]}.
    Uses regex parsing (faster than BS4 for 24MB response).
    """
    print("Fetching full dept list from adiga.kr...", flush=True)
    r = client.post(
        f"{BASE_URL}/ucp/prc/uni/admssUnivAjax.do",
        data={
            "_csrf": csrf,
            "menuId": MENU_ID,
            "cnrtYear": str(RESULT_YEAR),
            "unvCd": "",
            "unvSeCd": "10",
            "searchSyr": str(RESULT_YEAR),
            "pagination.cntPerPage": "100000",
        },
        timeout=120,
    )
    r.raise_for_status()
    print(f"Response size: {len(r.text):,} bytes — parsing...", flush=True)

    # Regex: find col02 <li unvCd="..." comScsbjtCd="..."> blocks and extract dept name
    # Attribute order in HTML is: class, style, unvCd, comScsbjtCd, index
    li_pattern = re.compile(
        r'<li\b[^>]*class="col02\b[^"]*"[^>]*unvCd="(\d+)"[^>]*comScsbjtCd="(\d+)"[^>]*>'
        r'(.*?)</li>',
        re.DOTALL | re.IGNORECASE,
    )
    span_pattern = re.compile(r'<span\b[^>]*class="body1\b[^"]*"[^>]*>(.*?)</span>', re.DOTALL)

    result: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()

    for m in li_pattern.finditer(r.text):
        unv_cd = m.group(1)
        com_cd = m.group(2)
        key = (unv_cd, com_cd)
        if key in seen:
            continue
        seen.add(key)
        # Extract dept name from span inside this li
        sm = span_pattern.search(m.group(3))
        dept_name = re.sub(r"<[^>]+>", "", sm.group(1)).strip() if sm else ""
        if unv_cd not in result:
            result[unv_cd] = []
        result[unv_cd].append({"comScsbjtCd": com_cd, "dept_name": dept_name})

    print(f"Found {sum(len(v) for v in result.values())} dept entries "
          f"across {len(result)} universities", flush=True)
    return result


# ── Detail parsing ─────────────────────────────────────────────────────────────

def parse_detail_html(html: str) -> list[dict]:
    """Parse admssUnivDetailLstAjax.do response → list of result records."""
    soup = BeautifulSoup(html, "html.parser")
    records = []

    # Each <div class="fldInner"> is one 전형 entry (may be display:none, doesn't matter)
    fld_inners = soup.find_all("div", class_="fldInner")
    for fld in fld_inners:
        lis = {" ".join(li.get("class", [])): li for li in fld.find_all("li")}

        # Process name: fldCol01 <a> text, strip "category > " prefix
        col01 = lis.get("fldCol01")
        if not col01:
            continue
        proc_raw = col01.get_text(separator=" ", strip=True)
        # Remove "학생부위주(교과) > " prefix if present
        if ">" in proc_raw:
            proc_raw = proc_raw.split(">")[-1].strip()
        process_name = proc_raw

        # 수시/정시 + 경쟁률
        col02 = lis.get("fldCol02")
        admission_type = None
        competition_rate = None
        if col02:
            ex_ty = col02.find("span", class_="exTy")
            if ex_ty:
                admission_type = ex_ty.get_text(strip=True)
                # Normalize: "정시(가)" → "정시", keep "수시"
                if "정시" in admission_type:
                    admission_type = "정시"
            gd_pt = col02.find("span", class_="gdPt")
            if gd_pt:
                try:
                    competition_rate = float(gd_pt.get_text(strip=True))
                except ValueError:
                    pass

        # cut_50
        col04 = lis.get("fldCol04_1")
        cut_50 = None
        if col04:
            gd = col04.find("span", class_="gdPt")
            if gd:
                try:
                    cut_50 = float(gd.get_text(strip=True))
                except ValueError:
                    pass

        # cut_70
        col05 = lis.get("fldCol05_1")
        cut_70 = None
        if col05:
            gd = col05.find("span", class_="gdPt")
            if gd:
                try:
                    cut_70 = float(gd.get_text(strip=True))
                except ValueError:
                    pass

        if not process_name:
            continue

        records.append({
            "process_name": process_name,
            "admission_type": admission_type,
            "competition_rate": competition_rate,
            "cut_50": cut_50,
            "cut_70": cut_70,
        })

    return records


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import sqlite3

    # Find missing universities
    import json as _json
    meta = _json.loads((DATA_DIR / "university_meta.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(str(DATA_DIR / "admission.db"))
    conn.row_factory = sqlite3.Row

    with_results = set(
        r[0] for r in conn.execute("""
            SELECT DISTINCT d.university FROM admission_result ar
            JOIN admission_department d ON ar.department_id = d.id
        """).fetchall()
    )
    missing_unis = sorted(set(meta.keys()) - with_results)
    print(f"Universities missing result data: {len(missing_unis)}")

    # Parse adiga.kr university codes
    unv_codes = parse_unv_codes()
    print(f"Parsed {len(unv_codes)} university codes from adiga_main.html")

    # Map missing universities to their adiga.kr unvCd
    missing_mapped: list[tuple[str, str]] = []
    for name in missing_unis:
        code = unv_codes.get(name)
        if code:
            missing_mapped.append((name, code))
        else:
            print(f"  [SKIP] No adiga.kr code for: {name}")
    print(f"Mapped {len(missing_mapped)}/{len(missing_unis)} missing universities to adiga codes")

    # Build set of target unvCd codes
    target_codes = {code for _, code in missing_mapped}
    name_by_code = {code: name for name, code in missing_mapped}

    # Create HTTP client
    client = httpx.Client(timeout=60, follow_redirects=True, headers=HEADERS)

    # Get CSRF token (use first university's page)
    first_code = missing_mapped[0][1]
    csrf = get_session_and_csrf(client, first_code)
    print(f"Got CSRF token: {csrf[:20]}...")

    # Get all dept codes
    all_depts = get_all_dept_codes(client, csrf)

    # Filter to only missing universities
    target_depts: dict[str, list[dict]] = {
        code: all_depts.get(code, [])
        for code in target_codes
    }
    total_depts = sum(len(v) for v in target_depts.values())
    print(f"Target departments: {total_depts} across {len(target_codes)} universities")

    # Build DB lookup: dept_name → department_id
    def get_or_create_dept_id(cursor: sqlite3.Connection, univ: str, dept_name: str) -> int | None:
        """Find department_id for a university+dept_name in admission_department."""
        rows = cursor.execute("""
            SELECT id FROM admission_department
            WHERE university = ? AND name = ?
            ORDER BY year DESC LIMIT 1
        """, (univ, dept_name)).fetchall()
        if rows:
            return rows[0][0]
        # Fuzzy: strip track suffix like "(주간)", "(야간)" and try again
        clean = re.sub(r"\(.*?\)$", "", dept_name).strip()
        if clean != dept_name:
            rows = cursor.execute("""
                SELECT id FROM admission_department
                WHERE university = ? AND name LIKE ?
                ORDER BY year DESC LIMIT 1
            """, (univ, f"{clean}%")).fetchall()
            if rows:
                return rows[0][0]
        # Create a placeholder dept (year=2025, no campus/track)
        cursor.execute("""
            INSERT OR IGNORE INTO admission_department (year, university, campus, track, name)
            VALUES (?, ?, '', '', ?)
        """, (RESULT_YEAR, univ, dept_name))
        conn.commit()
        row = cursor.execute("""
            SELECT id FROM admission_department WHERE year=? AND university=? AND name=?
        """, (RESULT_YEAR, univ, dept_name)).fetchone()
        return row[0] if row else None

    # Process each university's departments
    inserted = 0
    skipped = 0
    errors = 0

    for code, depts in target_depts.items():
        univ_name = name_by_code[code]
        if not depts:
            print(f"  [SKIP] {univ_name}: no depts found on adiga.kr")
            continue

        print(f"\n{'='*60}")
        print(f"  {univ_name} ({code}): {len(depts)} departments")

        for dept_info in depts:
            com_cd = dept_info["comScsbjtCd"]
            dept_name = dept_info["dept_name"]

            try:
                r = client.post(
                    f"{BASE_URL}/ucp/prc/uni/admssUnivDetailLstAjax.do",
                    data={
                        "_csrf": csrf,
                        "menuId": MENU_ID,
                        "cnrtYear": str(RESULT_YEAR),
                        "unvCd": code,
                        "unvSeCd": "10",
                        "searchSyr": str(RESULT_YEAR),
                        "comScsbjtCd": com_cd,
                    },
                    timeout=30,
                )
                r.raise_for_status()
            except Exception as e:
                print(f"    [ERR] {dept_name}: {e}")
                errors += 1
                time.sleep(DELAY)
                continue

            records = parse_detail_html(r.text)
            if not records:
                skipped += 1
                time.sleep(DELAY)
                continue

            dept_id = get_or_create_dept_id(conn, univ_name, dept_name)
            if dept_id is None:
                print(f"    [SKIP] Could not get/create dept_id for {dept_name}")
                skipped += 1
                time.sleep(DELAY)
                continue

            for rec in records:
                # Determine score_type
                adm_type = rec.get("admission_type") or ""
                if "수시" in adm_type:
                    score_type = "등급"
                    grade_type = "내신"
                else:
                    score_type = "등급"  # adiga.kr shows 등급 for 정시 too
                    grade_type = "수능등급"

                try:
                    conn.execute("""
                        INSERT INTO admission_result (
                            department_id, result_year, process_name, admission_type,
                            score_type, grade_type, competition_rate, cut_50, cut_70,
                            attributes
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{"source":"adiga"}')
                        ON CONFLICT(department_id, result_year, process_name) DO UPDATE SET
                            admission_type = COALESCE(excluded.admission_type, admission_type),
                            score_type = COALESCE(excluded.score_type, score_type),
                            grade_type = COALESCE(excluded.grade_type, grade_type),
                            competition_rate = COALESCE(excluded.competition_rate, competition_rate),
                            cut_50 = COALESCE(excluded.cut_50, cut_50),
                            cut_70 = COALESCE(excluded.cut_70, cut_70)
                    """, (
                        dept_id, RESULT_YEAR, rec["process_name"],
                        adm_type, score_type, grade_type,
                        rec.get("competition_rate"), rec.get("cut_50"), rec.get("cut_70"),
                    ))
                    inserted += 1
                except Exception as e:
                    print(f"    [DB ERR] {dept_name}/{rec['process_name']}: {e}")
                    errors += 1

            conn.commit()
            time.sleep(DELAY)

    print(f"\n{'='*60}")
    print(f"Done! Inserted: {inserted}, Skipped: {skipped}, Errors: {errors}")

    # Summary
    total_results = conn.execute("SELECT COUNT(*) FROM admission_result").fetchone()[0]
    print(f"Total admission_result records: {total_results}")
    conn.close()


if __name__ == "__main__":
    main()
