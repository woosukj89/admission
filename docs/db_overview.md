# Admission DB — Detailed Data Overview

Database: `data/admission.db` (SQLite 3.45.3)

---

## Table Summary

| Table | Rows | Description |
|-------|------|-------------|
| `admission_department` | 12,036 | Unique dept anchors (year × university × campus × name) |
| `admission_process` | 54,409 | 전형 records from 모집요강 PDFs |
| `admission_result` | 13,908 | 입시결과 records (score cutoffs, competition rates) |

Plus 2 FTS5 virtual tables (`admission_process_fts`, `admission_result_fts`) kept in sync via triggers.

---

## Table: `admission_department`

### Schema

| Column | Type | NOT NULL | Description |
|--------|------|----------|-------------|
| `id` | INTEGER PK | — | Auto-increment |
| `year` | INTEGER | ✓ | 모집요강 연도 the dept was parsed from |
| `university` | TEXT | ✓ | 대학교명 |
| `campus` | TEXT | | 캠퍼스 (stored as `''` not NULL to preserve UNIQUE constraint) |
| `track` | TEXT | | 계열 (인문, 자연, etc.) — sparsely populated |
| `name` | TEXT | ✓ | 학과/학부명 |

**UNIQUE:** `(year, university, campus, name)`

### Coverage

- 184 distinct universities
- Years in data: 2025 (majority), 2026 (가야대학교 — 2026 모집요강)
- `campus`: empty string `''` for most (only a few multi-campus universities actually populate this)
- `track`: populated inconsistently — some universities include 인문/자연 track, most don't

### Notes

- `campus = ''` is the standard for single-campus universities. Do **not** filter `WHERE campus IS NULL`.
- Year in this table refers to the **모집요강 연도** (e.g. `2025` means the 2025 입학 cycle guide), not the actual admission result year.
- Departments are shared between the process and result tables via `department_id`. The linkage is by (university, name) — not guaranteed to match across both sources (see Relationship section).

---

## Table: `admission_process`

Records extracted from 모집요강 PDFs. Each row represents one (department, process) pair.

### Schema

| Column | Type | NOT NULL | Description |
|--------|------|----------|-------------|
| `id` | INTEGER PK | — | Auto-increment |
| `department_id` | INTEGER FK | ✓ | → `admission_department.id` |
| `process_name` | TEXT | ✓ | 전형명 (e.g. `학생부교과`, `논술우수자`) |
| `process_type` | TEXT | | 전형 분류 (see below) |
| `admission_type` | TEXT | | `수시` or `정시` |
| `quota` | INTEGER | | 모집인원 |
| `content` | TEXT | | Structured header + raw PDF page text |
| `attributes` | TEXT | | JSON; primarily `{"계열": "..."}` |

**UNIQUE:** `(department_id, process_name)`

### process_type Distribution

| process_type | Count | % |
|---|---|---|
| 기타 | 32,551 | 59.8% |
| 학생부교과 | 8,432 | 15.5% |
| 학생부종합 | 7,980 | 14.7% |
| 실기/실적위주 | 2,915 | 5.4% |
| 수능위주 | 1,544 | 2.8% |
| 논술위주 | 987 | 1.8% |

Note: `기타` is high by design — it absorbs 기회균형, 농어촌, 특성화고, 사회배려, 국가보훈, 북한이탈, 장애인, 만학도, etc.

### admission_type Distribution

| admission_type | Count |
|---|---|
| 수시 | 38,923 (71.5%) |
| 정시 | 15,486 (28.5%) |

### `content` Field Format

Follows a consistent structured header, then the raw extracted PDF text:

```
대학: 건국대학교
모집단위: 컴퓨터공학부
계열: 자연
전형명: KU자기추천
전형유형: 학생부종합
모집시기: 수시
모집인원: 40명

--- 원문 (p12) ---
[raw PDF page text including tables, criteria, schedules, etc.]
```

The structured header lines are always present and machine-parseable. The `--- 원문 (p{N}) ---` section contains raw text from the source PDF page and may span multiple pages.

### `attributes` Field Format

Almost exclusively contains a single key `계열`:

```json
{"계열": "자연계열"}
{"계열": "인문사회계열"}
{"계열": "보 건 계 열"}
```

Note: 계열 values may contain spaces (from PDF extraction artifacts). Normalize with `REPLACE(계열, ' ', '')` when comparing.

---

## Table: `admission_result`

Records extracted from 입시결과 PDFs (from CDN and DuckDuckGo). Contains score cutoffs, competition rates, and quota.

### Schema

| Column | Type | NOT NULL | Fill Rate | Description |
|--------|------|----------|-----------|-------------|
| `id` | INTEGER PK | — | 100% | Auto-increment |
| `department_id` | INTEGER FK | ✓ | 100% | → `admission_department.id` |
| `result_year` | INTEGER | ✓ | 100% | 입시결과 연도 |
| `process_name` | TEXT | ✓ | 100% | 전형명 (as written in result PDF) |
| `admission_type` | TEXT | | 94.8% | `수시` or `정시` |
| `score_type` | TEXT | | 100% | Score measurement type |
| `competition_rate` | REAL | | ~80% | 경쟁률 |
| `average_score` | REAL | | 55.0% | 합격자 평균 |
| `cut_50` | REAL | | 10.6% | 50% 커트라인 |
| `cut_60` | REAL | | ~0% | 60% 커트라인 (only 1 row) |
| `cut_70` | REAL | | 21.6% | 70% 커트라인 ← **primary** |
| `cut_80` | REAL | | 1.7% | 80% 커트라인 |
| `cut_85` | REAL | | 3.3% | 85% 커트라인 |
| `cut_90` | REAL | | 4.0% | 90% 커트라인 |
| `content` | TEXT | | sparse | Raw extracted text |
| `attributes` | TEXT | | 100% | JSON; stores `{"모집인원": N}` |

**UNIQUE:** `(department_id, result_year, process_name)`

### score_type Distribution

| score_type | Count | Typical usage |
|---|---|---|
| 등급 | 11,681 (84.0%) | 수시 학생부 전형 |
| 표준점수 | 1,528 (11.0%) | 정시 수능 |
| 환산점수 | 507 (3.6%) | 대학별 환산 |
| 백분위 | 192 (1.4%) | 정시 수능 |

### result_year Distribution

| result_year | Count | Notes |
|---|---|---|
| 2021 | 45 | Very sparse |
| 2022 | 4 | Negligible |
| 2023 | 187 | Small sample |
| 2024 | 8,339 | **Primary dataset** |
| 2025 | 5,299 | **Primary dataset** |
| 2026 | 34 | Early release data |

For most analyses, filter to `result_year IN (2024, 2025)`.

### Cut Score Coverage (for `등급` rows only)

| Column | Non-null | Fill Rate (of 11,681 등급 rows) |
|---|---|---|
| `cut_70` | 2,752 | 23.6% |
| `average_score` | 7,652 | 65.5% |
| `cut_50` | 1,468 | 12.6% |
| `cut_85` | 454 | 3.9% |
| `cut_90` | 558 | 4.8% |
| `cut_80` | 231 | 2.0% |
| `cut_60` | 1 | 0.0% |

**Key insight:** `cut_70` is the most semantically important cutoff but is only populated for ~24% of 등급 rows. `average_score` has nearly 3× the coverage (65.5%) and is the primary fallback for scoring logic.

### `attributes` Field Format

Stores the quota extracted from the result PDF:

```json
{"모집인원": 40}
{"모집인원": 12}
```

Coverage: **81.6%** of all result rows (11,354 / 13,908). This is the most reliable source for quota — far better than joining with `admission_process` (see below).

### Score Interpretation

| score_type | Scale | Direction | Meaning |
|---|---|---|---|
| `등급` | 1.0–9.0 | **Lower = better** | 1 = top 4%, 9 = bottom |
| `표준점수` | ~100–150+ | Higher = better | Raw 수능 converted score |
| `백분위` | 0–100 | Higher = better | Percentile rank |
| `환산점수` | Varies | Higher = better | University-specific conversion |

Cut column semantics (using `cut_70` as example):
- `cut_70 = 2.5` means the 70th-percentile admitted student had a grade of 2.5
- Equivalently: 30% of admitted students had grade 2.5 or better (1.0–2.5)
- A student with grade 2.5 is **at** the cut_70 threshold → borderline safe

### Data Quality Notes

**Outlier values in `등급` rows:**
- `cut_70` has max=87.5 — several 서울과학기술대학교 art/sports depts (금속공예디자인학과, 도예학과, 스포츠과학과) have `score_type='등급'` but values in the 80-87.5 range. These are percentile-like scores mislabeled as 등급. Safe to filter with `WHERE r.cut_70 BETWEEN 1 AND 9.5`.
- `홍익대학교` 논술전형 has `cut_70 = 9.4` — just above the standard 1-9 range.

**NULL admission_type:** 729 rows (5.2%) have NULL `admission_type`, labeled as `(전형미상)`. These come from image-based PDF pages where section headers couldn't be extracted.

---

## Relationship: process_name Mismatch

**Critical:** The `process_name` strings in `admission_result` and `admission_process` are **not reliably joinable**.

| Join method | Matches | Match rate |
|---|---|---|
| Exact: `p.process_name = r.process_name` | 51 | 0.09% |
| Normalized (strip spaces): `REPLACE(p.process_name, ' ', '') = REPLACE(r.process_name, ' ', '')` | 690 | 1.3% |

**Why:** The two tables come from different PDF sources with different formatting:
- 모집요강 PDFs: spaced Korean text (`학 생 부 우 수 자`), full formal names
- 입시결과 PDFs: compact abbreviations (`학생부우수자`, `KU자기추천`, `가천바람개비`)

**Consequence:** You cannot reliably join `admission_process` and `admission_result` by `process_name`. The `department_id` FK is valid for both tables (they share the same department records), but the process records are effectively independent.

**Workaround:** Infer `process_type` from the `admission_result.process_name` string using regex classification (see `api.py → infer_process_type()`). Get quota from `json_extract(r.attributes, '$.모집인원')` (81.6% coverage) instead of joining `admission_process`.

---

## FTS5 Search Tables

Two FTS5 virtual tables for full-text search, kept in sync via INSERT/UPDATE/DELETE triggers:

```sql
-- admission_process_fts: columns (process_name, content_text, university, department_name)
-- admission_result_fts:  columns (process_name, content_text, university, department_name)
```

**Usage (table-valued function syntax — required for JOINs):**

```sql
SELECT t.*, d.university, d.name
FROM admission_result_fts('소프트웨어') fts
JOIN admission_result t ON t.id = fts.rowid
JOIN admission_department d ON d.id = t.department_id
ORDER BY fts.rank
LIMIT 20;
```

**Do NOT** use `WHERE fts_table MATCH ?` with JOINs — use the table-valued function form `FROM fts_table(?) fts`.

---

## Recommended Query Patterns

### Primary recommendation query (등급 scoring)

```sql
SELECT d.university, d.name AS department,
       r.process_name, r.admission_type, r.score_type,
       r.competition_rate, r.average_score,
       r.cut_70, r.cut_80, r.result_year,
       CAST(json_extract(r.attributes, '$.모집인원') AS INTEGER) AS quota
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.score_type = '등급'
  AND r.admission_type = '수시'
  AND (r.cut_70 IS NOT NULL OR r.average_score IS NOT NULL)
  AND r.result_year IN (2024, 2025)
ORDER BY CASE WHEN r.cut_70 IS NULL THEN 1 ELSE 0 END, r.cut_70 ASC;
```

### Finding safe schools (student grade 2.5, 등급)

```sql
-- 안정: cut_70 >= student_grade (student better than 70th percentile cut)
SELECT d.university, d.name, r.process_name, r.cut_70,
       ROUND(r.cut_70 - 2.5, 2) AS margin
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.score_type = '등급'
  AND r.admission_type = '수시'
  AND r.cut_70 >= 2.5
ORDER BY r.cut_70 - 2.5 ASC  -- smallest margin first = most competitive safe
LIMIT 20;
```

### Getting quota from result attributes

```sql
SELECT r.process_name,
       CAST(json_extract(r.attributes, '$.모집인원') AS INTEGER) AS quota,
       r.competition_rate,
       ROUND(CAST(json_extract(r.attributes, '$.모집인원') AS REAL) * r.competition_rate) AS applicants
FROM admission_result r
WHERE json_extract(r.attributes, '$.모집인원') IS NOT NULL
LIMIT 10;
```

### Department-level aggregation

```sql
SELECT d.university, d.name,
       COUNT(r.id) AS result_count,
       MIN(r.cut_70) AS best_cut70,
       AVG(r.average_score) AS avg_score
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.score_type = '등급'
GROUP BY d.university, d.name
HAVING result_count >= 2
ORDER BY best_cut70 ASC;
```

### Exploring 모집요강 content for a specific 전형

```sql
SELECT p.process_name, p.process_type, p.quota,
       SUBSTR(p.content, 1, 500) AS content_preview
FROM admission_process p
JOIN admission_department d ON d.id = p.department_id
WHERE d.university = '건국대학교'
  AND p.process_type = '학생부종합'
ORDER BY p.quota DESC;
```
