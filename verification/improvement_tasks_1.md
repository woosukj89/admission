# Improvement Task List
*Generated from `mcp_accuracy_report.md` — 2026-03-10*

---

## Summary

**Current state**: 6 test questions → only 2/6 partially answerable.
**Root causes** (in order of impact):
1. **62% of universities have zero results data** — biggest blocker
2. **모의고사 ≠ 내신 ≠ 수능** — system conflates three different grade types
3. **수능최저 unparsed** — can't validate actual admissibility
4. **13.6% process names unknown** — (전형미상) breaks type-based filtering
5. **Bugs**: `admission_type` mis-labelled, spaced dept names break search

Tasks are grouped into 5 tracks (A–E). Each task has: problem, what to do, files affected, complexity (S/M/L/XL), and blockers.

---

## Track A — Data Acquisition (Coverage)

### A1 · Download 입시결과 PDFs for missing major universities
- [x] Done (partial — 서강대 108 records, 한양대 51 records via OCR; 경희대/가톨릭대/서울여대/성신여대 PDF formats incompatible with generic OCR extractor)
**Priority**: 🔴 Critical
**Complexity**: L
**Blocks**: A2, B1, B2, C1

**Problem**: 113 universities (62%) have no results. The most consequential missing schools are:
- Tier 1 (0 results): 서울대, 고려대, 연세대
- Tier 2 (0 results): 서강대, 한양대, 충남대, 충북대
- Tier 3 (0 results): 경희대, 숭실대, 성신여대, 서울여대, 인하대, 영남대, 조선대 등

**Current download logic** (`download_results.py`): Only checks one CDN (`cdn013.negagea.net`). Most major universities are not on this CDN.

**What to do**:
1. Audit CDN coverage: cross-check `data/results_report.json` against the 113-uni list
2. For each missing top-30 university by tier, manually locate the 입시결과 PDF URL on their admission website
3. Extend `download_results.py` with a second URL list: `MANUAL_URLS = {university: url}` for direct downloads
4. Target universities to add manually (by priority):
   - 경희대: https://iphak.khu.ac.kr (공지사항 → 입시결과)
   - 중앙대: 전형미상 문제 해결을 위한 공식 파일 재수집
   - 숭실대, 성신여대, 서울여대, 인하대, 영남대 등
5. For 서울대/고려대/연세대: check each school's official admission office (not CDN)

**Files**: `download_results.py`, `data/results_report.json`
**Output**: More files under `data/results/`

---

### A2 · Re-extract and re-import newly downloaded results
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: S (automated once A1 is done)
**Blocked by**: A1

**Problem**: Newly downloaded PDFs need to go through the existing extraction pipeline.

**What to do**:
1. Run `extract_results_pdfs.py` on new files → `data/results_extracted/`
2. Run `extract_results_batch.py` on new JSONs → import into `admission_result` table
3. Verify via: `SELECT COUNT(DISTINCT university) FROM admission_result r JOIN admission_department d ON d.id = r.department_id`

**Files**: `extract_results_pdfs.py`, `extract_results_batch.py`

---

### A3 · Investigate adiga.kr for 입시결과 data
- [x] Done (investigated — adiga.kr only has 모집요강, not 입시결과 PDF links)
**Priority**: 🟡 High
**Complexity**: M

**Problem**: adiga.kr is already used for 모집요강. It may also expose 입시결과 data for universities missing from CDN.

**What to do**:
1. Check `adiga_main.html` and adiga.kr site structure for an 입시결과 section
2. If available, check if data is downloadable as PDF or structured data
3. Extend `download_adiga.py` or create `download_adiga_results.py` if feasible
4. Note: adiga.kr is a government portal — data may be more complete than CDN

**Files**: `download_adiga.py`, potentially new `download_adiga_results.py`

---

### A4 · Investigate 대학알리미 public data API
- [x] Done (investigated — academyinfo.go.kr does not expose per-student cut score data; only enrollment totals)
**Priority**: 🟡 High
**Complexity**: M

**Problem**: `academyinfo.go.kr` is a government open data portal with structured university data including enrollment statistics. It may have 합격자 성적 정보.

**What to do**:
1. Check https://www.academyinfo.go.kr → 공시정보 → 학생 선발 정보
2. Try: https://www.data.go.kr for API (공공데이터포털, "대학 입학전형 결과")
3. If data is machine-readable, write `download_academyinfo.py`
4. Map to existing university names; handle discrepancies in naming

**Files**: new `download_academyinfo.py`

---

## Track B — Data Quality Bugs

### B1 · Fix `admission_type` mis-labelling in `extract_results_batch.py`
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: S

**Problem**: 건국대학교 컴퓨터공학부 KU지역균형 (명백히 수시 전형)이 DB에 `admission_type='정시'`로 저장됨. 실제 파일 텍스트에는 "Ⅰ 2024학년도 전형결과 ― **수시** 학생부교과전형"으로 명시되어 있음. 다른 대학도 동일 버그 가능성 있음.

**Root cause** (to investigate):
- `extract_results_batch.py`의 `admission_type` 결정 로직이 page-level 텍스트("수시"/"정시")를 읽지 못하거나 기본값이 잘못 설정됨
- 또는 섹션 구분 경계를 잘못 처리 (수시 PDF에서 정시를 읽어버림)

**What to do**:
1. Open `extract_results_batch.py`, find where `admission_type` is assigned
2. Check: does it read from the file name (e.g., `_수시입시결과.pdf`)? Does it parse page text?
3. If using page text: confirm pattern matching for "수시" / "정시" section headers
4. If using filename: verify filename parsing regex handles all patterns
5. Run re-extraction on 건국대 and spot-check 5 other universities post-fix
6. After fix, delete and re-insert affected records in DB

**Files**: `extract_results_batch.py`

---

### B2 · Fix (전형미상) — improve `find_process_context_from_text()`
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: M
**Blocked by**: (none — can work on existing data in parallel)

**Problem**: 1,891 records (13.6%) have `process_name = '(전형미상)'`. This means the page-level section header parser failed to find a 전형명. The entire 중앙대 dataset (110 records) and other major schools fall into this bucket.

**Investigation needed**:
1. Sample 20 `(전형미상)` records — check their source pages in `data/results_extracted/`
2. Read the raw `text` field from those pages; identify what patterns exist that the current parser misses
3. Current patterns (from memory): `■`, `▷`, `[전형명]` in page headers
4. Missing patterns likely include:
   - Table-embedded 전형명 columns (e.g., "전형명" column in table header)
   - Roman numeral section headers: `Ⅱ. 학생부종합전형`
   - Bold/underline markdown artifacts: `**논술전형**`
   - Multi-line headers split across rows

**What to do**:
1. Add pattern: search for `전형명` **inside table column headers** — if a column contains 전형명, use the cell values as context
2. Add pattern: `Ⅰ`, `Ⅱ`, `Ⅲ` Roman numerals before 전형 name (e.g. `Ⅱ. 학생부종합전형 결과`)
3. Add fallback: if `process_name` in the table itself (from 모집요강 matching), use that
4. Add fallback: if `(전형미상)` appears for a university that has only one 전형 type in the 모집요강, inherit that name
5. Re-extract affected universities, re-import into DB

**Files**: `extract_results_batch.py` (function `find_process_context_from_text()`), `extract_results_pdfs.py`

---

### B3 · Fix spaced department names in search
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: Some department names are stored with inserted spaces: `"정 보 통 신 공 학 과"`, `"국 어 교 육 과"`. These are invisible to LIKE-based search (`%정보통신%` won't match).

**Root cause**: pdfplumber extracts character-level text with spaces between each character for some fonts (especially in older or low-quality PDFs).

**What to do**:
1. In `extract_all_pdfs.py` or the pdfplumber extraction step, add a post-processing function:
   ```python
   def collapse_spaced_text(text: str) -> str:
       # If every other char is a space and non-space chars are all Korean/alpha,
       # collapse them. E.g. "정 보 통 신" → "정보통신"
       import re
       if re.fullmatch(r'(\S )+\S', text.strip()):
           return text.replace(' ', '')
       return text
   ```
2. Apply to `admission_department.name` during upsert
3. Also add a normalized column or FTS synonym to handle legacy data
4. Alternative: run a one-time SQL update to fix existing records with this pattern
5. Verify: `SELECT name FROM admission_department WHERE name LIKE '% % %' AND name LIKE '%과%'` and review

**Files**: `extract_all_pdfs.py`, `extract_admission_batch.py`, one-time migration SQL

---

### B4 · Parse and store 충원인원 (wait-list fill count)
- [x] Done
**Priority**: 🟡 High
**Complexity**: M
**Blocked by**: B1 (reliable admission_type needed first)

**Problem**: Actual PDF files contain 충원인원 (e.g., 건국대 컴공: 76명 충원 with only 21명 모집). This makes real acceptance rates much lower than face-value competition ratios, but the data is not stored.

**Why it matters**: "합격 가능성" Q4/Q5 is much more accurate with this info. A 8:1 competition ratio with 76 wait-list fills means many more people actually get in.

**What to do**:
1. Audit existing JSON files: confirm `충원인원` column is present and parseable (confirmed in 건국대)
2. In `extract_results_batch.py`, extend `build_col_map()` to detect `충원인원` column labels (variants: `충원`, `충원합격`, `추가합격`)
3. Store as: `admission_result.attributes["충원인원"] = N`
4. Add to MCP `get_process_detail` response: `"충원인원"` field
5. Add derived field: `"실질_경쟁률" = round(competition_rate * quota / (quota + 충원인원), 2)`

**Files**: `extract_results_batch.py`, `src/mcp_server.py`

---

### B5 · Handle old/stale result years
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: S

**Problem**: DB contains `result_year` values of 2021, 2022, 2023 mixed with 2024/2025. For a student applying now, 2021 data is 4 years old and may be very misleading (입결 has shifted significantly).

**What to do**:
1. In `match_by_grade` and `search_programs`, default to filtering `result_year >= 2024`
2. Add optional parameter: `result_year: int | None = None` (default to latest available)
3. In `get_process_detail`, return results grouped by year with the latest year first
4. Display year prominently in all tool outputs

**Files**: `src/mcp_server.py`

---

## Track C — New Data Fields (Parsing)

### C1 · Parse 수능최저학력기준 from 모집요강 content
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: L
**Blocked by**: (existing `data/extracted/` already available)

**Problem**: 모집요강 content text has 수능최저 conditions like "국수영탐 중 2개 합 5 이내" but these are never extracted. Without this, the system can't tell students whether they actually qualify for a specific 전형.

**Why it matters**: A student with 수능 국3+수2+영1+탐4 = 10 cannot satisfy "2개 합 5이내" regardless of their 내신. This eliminates many 전형 from consideration.

**Parsing patterns to handle**:
```
수능최저 없음 → {"있음": false}
국수영탐 중 2개 합 5이내 → {"과목수": 2, "합": 5, "방식": "합"}
국수영 중 2개 각 2등급 → {"과목수": 2, "각": 2, "방식": "각"}
영어 2등급 이상 → {"영어": 2, "방식": "필수"}
한국사 4등급 이상 → {"한국사": 4}
```

**What to do**:
1. Create `parse_수능최저.py` with regex patterns covering all common formats:
   - `수능 최저학력기준 없음` / `수능최저 없음`
   - `[과목목록] 중 N개 합 M이내`
   - `[과목목록] 중 N개 각 M등급 이상`
   - `영어 N등급 이상`
   - `한국사 N등급 이내`
2. Apply to each `admission_process.content` during or after extraction
3. Store as `attributes["수능최저"] = {...}` — structured dict
4. Add a new MCP tool or extend `get_process_detail` to return `수능최저` info
5. Optionally: add `check_수능최저(student_grades: dict, process_id: int) -> bool` utility

**Files**: new `parse_수능최저.py`, `extract_admission_batch.py`, `src/mcp_server.py`

---

### C2 · Tag 지역인재 전형
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: 지역인재 전형(경북대 교과지역, 전남대 지역인재 등)은 해당 지역 고교 출신만 지원 가능. 현재 시스템에서 일반 전형과 혼재되어 수도권 학생에게 부정확한 추천을 함.

**What to do**:
1. In `extract_results_batch.py` or as a post-processing step, detect 지역인재 전형:
   - 전형명에 `지역인재`, `지역균형`, `지역`, `고른기회` 등 포함
   - 단, `KU지역균형` (건국대) 같은 것은 지역인재 아님 — 학교별 확인 필요
2. Store as `attributes["지역인재"] = true/false`
3. In `match_by_grade` and `search_programs`, add optional `exclude_regional: bool = False` parameter
4. When `exclude_regional=True`, filter out `attributes["지역인재"] = true` records

**Files**: `extract_results_batch.py`, `src/mcp_server.py`

---

### C3 · Parse 전형요소 반영 비율 (내신 가중치)
- [x] Done
**Priority**: 🟡 High
**Complexity**: L

**Problem**: Different 전형s weight 내신 differently. 학생부교과 전형 might be 100% 내신, while 학생부종합 is 서류 60% + 면접 40%. Without this, we can't correctly weight the student's 내신 grade.

**Parsing patterns in 모집요강**:
```
교과 100%
학생부 교과 80% + 출결 20%
서류 60% + 면접 40%
1단계: 서류 100%(4배수) / 2단계: 서류 70% + 면접 30%
```

**What to do**:
1. Add to `parse_수능최저.py` (or separate file): regex for 전형요소 비율
2. Store as `attributes["전형요소"] = {"교과": 80, "출결": 20}` etc.
3. Use in `match_by_grade` when computing effective grade:
   - 교과 100%: use 내신 직접 비교
   - 서류+면접: 내신은 참고 수준, 기타 역량 필요
4. Display in `get_process_detail` response

**Files**: `extract_admission_batch.py`, `src/mcp_server.py`

---

## Track D — MCP Tool Improvements

### D1 · Separate 내신 / 수능 grade types in `match_by_grade`
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: M
**Blocked by**: (can be done now with existing data)

**Problem**: `match_by_grade(grade=3.0, score_type="등급")` compares 3.0 against 학생부 내신 cuts for 수시, AND against 수능 등급 cuts for 정시, treating them identically. But:
- 내신 3등급 ≠ 수능 3등급 (completely different scales and distributions)
- A student might have 내신 2.5 and 수능 3등급, or vice versa

**What to do**:
1. Rename current `score_type` options to disambiguate:
   - `"내신"` → compare against `admission_type='수시'` cuts (학생부교과)
   - `"수능등급"` → compare against `admission_type='정시'` cuts (등급형)
   - `"표준점수"` → compare against `score_type='표준점수'` cuts
   - `"백분위"` → compare against `score_type='백분위'` cuts
2. Add input parameter: `grade_type: str = "내신"` (separate from `score_type`)
3. If `grade_type="내신"`: only query `admission_type IN ('수시', NULL)` results with `score_type='등급'`
4. If `grade_type="수능등급"`: primarily query `admission_type='정시'` results
5. If `grade_type="수능표준점수"`: query `score_type='표준점수'`
6. Update all tool descriptions to clearly state what grade type they expect

**Files**: `src/mcp_server.py`

---

### D2 · Add subject-specific grade input
- [x] Done
**Priority**: 🟡 High
**Complexity**: M
**Blocked by**: C3 (전형요소 비율 data needed for proper weighting)

**Problem**: A student with 국3/수2/영1 has very different 전형 fit than a student with 국1/수4/영2, even if their average is the same. The current `match_by_grade(grade=2.0)` cannot distinguish them.

**What to do**:
1. Add new MCP tool: `match_by_subjects`
   ```python
   def match_by_subjects(
       korean: float | None = None,
       math: float | None = None,
       english: float | None = None,
       science: float | None = None,
       social: float | None = None,
       grade_type: str = "내신",  # "내신" or "수능등급"
       region: str | None = None,
       major_keywords: list[str] = [],
       limit: int = 30,
   ) -> list[dict]: ...
   ```
2. For each queried process, compute effective grade based on available 전형요소 data:
   - If 전형요소 not known: use simple average of provided subjects
   - If 전형요소 known: weight accordingly
3. Returns same fields as `match_by_grade` plus `effective_grade` and `grade_breakdown`
4. Particularly useful for: identifying 전형s where the student's strongest subject is weighted highest

**Files**: `src/mcp_server.py`

---

### D3 · Add acceptance probability % to `match_by_grade`
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: S
**Blocked by**: (B4 충원인원 optional but improves accuracy)

**Problem**: `verdict` (안정/추천/도전) is coarse. A student wants to know "am I at the 60% line or the 90% line?"

**What to do**:
1. When cut_50, cut_70, and cut_90 are all available, interpolate position:
   ```python
   def estimate_percentile(grade: float, cut_50, cut_70, cut_90) -> float:
       # For 등급: lower is better, so invert logic
       if grade <= cut_50:
           return 95.0  # top 5%
       elif grade <= cut_70:
           pct = 50 + (cut_70 - grade) / (cut_70 - cut_50) * 20
           return round(pct, 1)
       elif grade <= cut_90:
           pct = 10 + (cut_90 - grade) / (cut_90 - cut_70) * 40
           return round(pct, 1)
       else:
           return 5.0  # below 90% cut
   ```
2. Return `"acceptance_pct": 72.3` alongside `verdict`
3. When only cut_70 is available: return null with note "insufficient data"
4. Display as: `"안정 (상위 약 25% 해당)"`

**Files**: `src/mcp_server.py`

---

### D4 · Improve 계열 (인문/자연) filtering in `search_programs`
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: `track` column is inconsistent ("자연 과학", "공학", "자연", "이공계", "과학"). Natural science programs are missed when filtering by 자연계.

**What to do**:
1. Add `classify_track(dept_name: str, track: str) -> str` function:
   ```python
   SCIENCE_KEYWORDS = ["공학", "과학", "수학", "물리", "화학", "생물", "컴퓨터",
                       "전기", "전자", "기계", "반도체", "소프트웨어", "건축", "토목"]
   HUMANITIES_KEYWORDS = ["국어", "영어", "역사", "철학", "문학", "사학", "어문",
                          "심리", "사회", "정치", "경제", "경영", "법학"]
   ```
2. Normalize `track` values on insert (or as a computed column)
3. Add `track` filter to `search_programs`: `track: str | None = None` accepting `"자연"` / `"인문"` / `"예체능"` / `"의약학"`
4. Use the classifier to filter both by explicit `track` and by `dept_name` keywords

**Files**: `src/mcp_server.py`, `src/storage/admission_store.py` (optional: add computed field)

---

### D5 · Expand 유사학과 keyword mapping
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: S

**Problem**: `search_programs(["컴퓨터공학"])` misses "컴퓨터정보공학부", "소프트웨어학부", "IT공학과", "AI학과", "디지털미디어공학" — all equivalent from a student's perspective.

**What to do**:
1. Expand `CATEGORY_KEYWORDS` in `src/mcp_server.py` with more aliases per category
2. Add a new approach: prefix-based expansion
   - "컴퓨터" matches: 컴퓨터공학, 컴퓨터정보, 컴퓨터정보공학, 컴퓨터과학
   - But the DB's `find_departments(name="컴퓨터")` already does LIKE `%컴퓨터%`
3. The real gap is cross-category synonyms. Add a `SYNONYMS` dict:
   ```python
   SYNONYMS = {
       "컴퓨터공학": ["소프트웨어", "IT공학", "정보공학", "전산학"],
       "의예과": ["의학과", "의학전문대학원"],
       "경영학": ["경영정보", "비즈니스"],
       ...
   }
   ```
4. Apply synonym expansion in `_expand_keywords()` before querying DB

**Files**: `src/mcp_server.py`

---

### D6 · Add `required_improvement` field to `get_process_detail`
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: Q4 asks "무슨 성적을 더 높여야 해?" — the system gives a verdict but not the gap.

**What to do**:
1. In `get_process_detail`, when called with `student_grade` parameter (optional), compute:
   ```python
   gap_to_cut70 = cut_70 - student_grade  # positive = student above cut
   gap_to_cut50 = cut_50 - student_grade if cut_50 else None
   ```
2. Return:
   ```json
   {
     "required_improvement": {
       "to_추천": round(student_grade - cut_70, 2),
       "to_안정": round(student_grade - (cut_70 - 0.5), 2),
       "verdict": "도전",
       "summary": "현재 1.25등급 부족. cut_70 달성하려면 내신을 1.75등급까지 올려야 함."
     }
   }
   ```
3. Add optional `student_grade: float | None = None` parameter to `get_process_detail`

**Files**: `src/mcp_server.py`

---

### D7 · Add `year_trend` to `get_process_detail`
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: S
**Blocked by**: A1 (more years of data needed to be meaningful)

**Problem**: Q4/Q5 needs context on whether the 전형's cut score is rising or falling. A school that was cut_70=3.0 in 2022 but 2.5 in 2024 is getting harder.

**What to do**:
1. In `get_process_detail`, query `admission_result` for the same `university`+`process_name` across all available years
2. Return a `year_trend` array:
   ```json
   "year_trend": [
     {"year": 2022, "cut_70": 3.2, "cut_50": 3.0},
     {"year": 2023, "cut_70": 2.9, "cut_50": 2.7},
     {"year": 2024, "cut_70": 2.7, "cut_50": 2.5}
   ]
   ```
3. Compute `trend_direction`: `"rising"` (getting harder), `"falling"`, `"stable"`

**Files**: `src/mcp_server.py`

---

### D8 · Add 수능최저 display to all result-returning tools
- [x] Done
**Priority**: 🔴 Critical (once C1 is done)
**Complexity**: S
**Blocked by**: C1

**Problem**: Even after parsing 수능최저 (C1), it won't appear in MCP output unless added.

**What to do**:
1. In `search_programs` and `match_by_grade`: join with `admission_process` table to include `attributes["수능최저"]` for each result
2. This requires matching `admission_result` records to `admission_process` records by `(university, process_name)` — currently not directly linked
3. The match should be by `process_name` LIKE (fuzzy, since 입결 process names and 모집요강 process names may differ slightly)
4. Display as: `"수능최저": "국수영탐 중 2개 합 5이내"` or `"수능최저": null` (없음)

**Files**: `src/mcp_server.py`, potentially `src/storage/admission_store.py`

---

### D9 · Add `compare_universities` tool
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: M
**Blocked by**: A1 (more data needed)

**Problem**: Q3 (전략) requires knowing how different institutions' cut scores compare across multiple tiers simultaneously. No tool does side-by-side comparison.

**What to do**:
1. New MCP tool:
   ```python
   def compare_universities(
       department_keyword: str,
       universities: list[str] | None = None,  # specific unis, or None = all
       region: str | None = None,
       max_tier: int | None = None,
       score_type: str = "등급",
   ) -> list[dict]: ...
   ```
2. Returns one row per university with: `{university, tier, region, cut_50, cut_70, 경쟁률, 충원인원, 전형명}`
3. Sorted by `cut_70 ASC` (most selective first) — gives the "ranking by actual 입결"
4. This directly answers "전국 컴퓨터공학 입결 순"

**Files**: `src/mcp_server.py`

---

## Track E — Architecture / Schema Changes

### E1 · Add `grade_type` column to `admission_result`
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: M
**Blocked by**: B1 (clean `admission_type` needed first)

**Problem**: The same `score_type='등급'` column is used for both 내신 (수시) and 수능등급 (정시). But 내신 3등급 ≠ 수능 3등급. There is no column distinguishing them.

**What to do**:
1. Add a new column: `grade_type TEXT` to `admission_result`
   - Values: `"내신"` (학생부 교과 등급), `"수능등급"` (수능 원점수 등급), `"표준점수"`, `"백분위"`, `"환산점수"`
2. Backfill from `score_type` + `admission_type`:
   ```sql
   UPDATE admission_result
   SET grade_type = CASE
     WHEN score_type = '등급' AND admission_type = '수시' THEN '내신'
     WHEN score_type = '등급' AND admission_type = '정시' THEN '수능등급'
     WHEN score_type IN ('표준점수', '백분위', '환산점수') THEN score_type
     ELSE score_type
   END
   ```
3. Update `find_results_by_score()` and `match_by_grade` to filter by `grade_type`
4. Update `AdmissionStore._init_db()` to add column migration if missing

**Files**: `src/storage/admission_store.py`, `src/mcp_server.py`

---

### E2 · Link `admission_result` to `admission_process` by process_name
- [x] Done
**Priority**: 🟡 High
**Complexity**: M
**Blocked by**: B2 (전형미상 fix needed first for reliable matching)

**Problem**: `admission_result` and `admission_process` share `process_name` and `department_id` but there is no foreign key. This means:
- 수능최저 from `admission_process.attributes` cannot be joined to `admission_result`
- `전형요소 비율` similarly disconnected

**What to do**:
1. Add optional column: `process_id INTEGER REFERENCES admission_process(id)` to `admission_result`
2. Populate via: `UPDATE admission_result r SET process_id = p.id FROM admission_process p WHERE p.department_id = r.department_id AND p.process_name = r.process_name`
3. For (전형미상) records: attempt fuzzy match; leave NULL if ambiguous
4. Update `find_results()` to include `attributes` from linked process when available

**Files**: `src/storage/admission_store.py`

---

### E3 · Add 수능 표준점수 ↔ 등급 conversion reference table
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: System cannot convert a student's "수학 2등급 모의고사" to the approximate 표준점수 range needed for 정시 matching.

**What to do**:
1. Create `data/suneung_grade_table.json`:
   ```json
   {
     "2025": {
       "국어": {"1": [131, 150], "2": [122, 130], "3": [112, 121], ...},
       "수학": {"1": [134, 150], "2": [123, 133], ...},
       "영어": {"1": "절대평가 90점 이상", ...},
       "사탐_평균": {"1": [65, 70], "2": [60, 64], ...},
       "과탐_평균": {"1": [67, 70], "2": [62, 66], ...}
     }
   }
   ```
2. Load in `src/mcp_server.py` at startup
3. Add utility: `grade_to_score_range(subject, grade, year) -> (min, max)`
4. Use in `match_by_grade` when input is `grade_type="수능등급"` to query `score_type='표준점수'` records within that range
5. Source: KICE 수능 등급 컷 자료 (공개 데이터)

**Files**: new `data/suneung_grade_table.json`, `src/mcp_server.py`

---

## Task Summary Table

| Done | ID | Title | Priority | Complexity | Blocked by |
|------|----|-------|----------|------------|------------|
| 🔄 | A1 | Download missing 입시결과 PDFs (OCR) | 🔴 Critical | L | — |
| ✅ | A2 | Re-extract newly downloaded results | 🔴 Critical | S | A1 |
| ✅ | A3 | Investigate adiga.kr 입시결과 | 🟡 High | M | — |
| ✅ | A4 | Investigate 대학알리미 public API | 🟡 High | M | — |
| ✅ | B1 | Fix admission_type mis-labelling bug | 🔴 Critical | S | — |
| ✅ | B2 | Fix (전형미상) process name parsing | 🔴 Critical | M | — |
| ✅ | B3 | Fix spaced department names | 🟡 High | S | — |
| ✅ | B4 | Parse 충원인원 | 🟡 High | M | B1 |
| ✅ | B5 | Handle old result years | 🟠 Medium | S | — |
| ✅ | C1 | Parse 수능최저 from 모집요강 | 🔴 Critical | L | — |
| ✅ | C2 | Tag 지역인재 전형 | 🟡 High | S | — |
| ☐ | C3 | Parse 전형요소 반영 비율 | 🟡 High | L | — |
| ✅ | D1 | Separate 내신/수능 in match_by_grade | 🔴 Critical | M | — |
| ☐ | D2 | Add subject-specific grade input tool | 🟡 High | M | C3 |
| ✅ | D3 | Add acceptance probability % | 🟠 Medium | S | B4 |
| ✅ | D4 | Improve 계열 filtering | 🟡 High | S | — |
| ✅ | D5 | Expand 유사학과 keyword mapping | 🟠 Medium | S | — |
| ✅ | D6 | Add required_improvement to detail | 🟡 High | S | — |
| ✅ | D7 | Add year_trend to detail | 🟠 Medium | S | A1 |
| ✅ | D8 | Display 수능최저 in tool outputs | 🔴 Critical | S | C1 |
| ✅ | D9 | Add compare_universities tool | 🟠 Medium | M | A1 |
| ✅ | E1 | Add grade_type column to DB | 🔴 Critical | M | B1 |
| ✅ | E2 | Link admission_result to admission_process | 🟡 High | M | B2 |
| ✅ | E3 | Add 수능 grade ↔ 표준점수 table | 🟡 High | S | — |

---

## Recommended Implementation Order

### Sprint 1 — Bugs + Quick Wins (1–2 days)
`B1` → `B3` → `B5` → `D1` → `D6`
*Result: Fixes data integrity bugs, stops 모의고사/내신 confusion, adds gap output*

### Sprint 2 — (전형미상) Fix (2–3 days)
`B2` → `E2` (partial)
*Result: -13.6% unknown process names, better join capability*

### Sprint 3 — New Data Fields (3–5 days)
`C1` → `D8` → `C2` → `B4` → `D3`
*Result: 수능최저 visible, 지역인재 filtered, acceptance % shown*

### Sprint 4 — Data Acquisition (1–2 weeks, mostly manual effort)
`A1` → `A2` → `A3` → `A4`
*Result: Coverage grows from 38% toward 60–80%*

### Sprint 5 — Advanced Tools (3–5 days)
`E1` → `E3` → `D2` → `D4` → `D5` → `D9`
*Result: Subject-specific matching, 자연계 filtering, full comparison tool*

### Sprint 6 — Nice-to-haves (ongoing)
`C3` → `D7` → `B5` cleanup
