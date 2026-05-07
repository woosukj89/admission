# Admission API & DB Schema Reference

## Contents
1. [API Reference](#api-reference)
2. [DB Schema](#db-schema)
3. [Query Examples](#query-examples)

---

## API Reference

### Base URL
```
http://localhost:8000
```

### `GET /recommend`

Returns top 5 안정 (safe) and 5 도전 (reach) university recommendations based on student parameters.

#### Query Parameters

| Parameter | Type | Range | Description |
|-----------|------|-------|-------------|
| `year` | int | — | 입시결과 연도 필터 (e.g. `2025`) |
| `susi_grade` | float | 1.0–9.0 | 수시 내신 평균등급 (낮을수록 우수) |
| `jeongsi_grade` | float | 1.0–9.0 | 정시 수능 평균등급 (낮을수록 우수) |
| `departments` | string | — | 학과 키워드, 쉼표 구분 (e.g. `컴퓨터공학,소프트웨어`) |
| `regions` | string | — | 지역 키워드, 쉼표 구분 (e.g. `서울,경기`) |

**All parameters are optional.**

Supported region keywords: `서울`, `경기`, `인천`, `강원`, `충청`, `전라`, `경상`, `제주`
- `충청` covers: 충남, 충북, 대전, 세종
- `전라` covers: 전남, 전북, 광주
- `경상` covers: 경남, 경북, 대구, 부산, 울산

#### Response Shape

```json
{
  "safe": [ ...up to 5 items... ],
  "reach": [ ...up to 5 items... ],
  "total_candidates": 142,
  "query_summary": "수시 내신 2.5등급 | 학과: 컴퓨터공학 | 지역: 서울"
}
```

#### Response Item

```json
{
  "university": "건국대학교",
  "department": "컴퓨터공학부",
  "process_name": "KU자기추천",
  "admission_type": "수시",
  "score_type": "등급",
  "competition_rate": 12.4,
  "quota": 50,
  "applicants": 621,
  "average_score": 2.1,
  "cut_70": 2.3,
  "cut_80": 2.6,
  "result_year": 2025,
  "verdict": "안정"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `university` | string | 대학교명 |
| `department` | string | 학과/학부명 |
| `process_name` | string | 전형명 |
| `admission_type` | string\|null | `수시` or `정시` |
| `score_type` | string\|null | `등급`, `표준점수`, `환산점수`, `백분위` |
| `competition_rate` | float\|null | 경쟁률 (지원자 / 모집정원) |
| `quota` | int\|null | 모집정원 (process 테이블에서; 대부분 null) |
| `applicants` | int\|null | 지원자 수 = `quota × competition_rate` |
| `average_score` | float\|null | 합격자 평균 등급 |
| `cut_70` | float\|null | 70% 커트라인 등급 |
| `cut_80` | float\|null | 80% 커트라인 등급 |
| `result_year` | int | 입시결과 연도 |
| `verdict` | string | `안정` / `도전` / `참고` (성적 미입력 시) |

#### Recommendation Algorithm

**For 등급 scores (lower = better):**

안정 (safe) — student is at or better than the 70% cutoff:
```
cut_70 IS NOT NULL AND cut_70 >= student_grade
```
Fallback (no cut_70):
```
average_score >= student_grade + 0.3
```

도전 (reach) — outside cut_70 but within striking distance:
```
cut_70 < student_grade AND (cut_80 >= student_grade OR average_score >= student_grade - 0.5)
```
Fallback:
```
average_score >= student_grade - 0.3 AND average_score < student_grade + 0.3
```

**Sorting:**
- 안정: `cut_70 - student_grade ASC` (most competitive safe school first)
- 도전: `student_grade - cut_70 ASC` (closest reach school first)
- No grade given: `competition_rate DESC`

**When no grade is given:** returns the 10 most competitive results (by competition_rate) regardless of 안정/도전 classification, with `verdict: "참고"`.

#### `curl` Examples

```bash
# 수시 내신 2.5등급, 서울 컴퓨터공학
curl "http://localhost:8000/recommend?susi_grade=2.5&departments=컴퓨터공학&regions=서울"

# 정시 수능 2.8등급, 경영학 서울/경기
curl "http://localhost:8000/recommend?jeongsi_grade=2.8&departments=경영&regions=서울,경기"

# 수시+정시 동시, 전국 간호학과
curl "http://localhost:8000/recommend?susi_grade=2.0&jeongsi_grade=2.5&departments=간호"

# 2025년 결과만 필터
curl "http://localhost:8000/recommend?susi_grade=3.5&year=2025&regions=경상"

# 성적 없이 지역+학과만 (경쟁률 순)
curl "http://localhost:8000/recommend?departments=의예과&regions=서울"
```

---

### `GET /stats`

Returns a summary of what's in the admission database.

#### Response

```json
{
  "universities": 184,
  "departments": 12020,
  "processes": 54409,
  "results": 13908,
  "result_years": [2024, 2025],
  "score_types": {
    "등급": 11681,
    "표준점수": 1282,
    "환산점수": 438,
    "백분위": 215,
    null: 292
  },
  "admission_types": {
    "수시": 9842,
    "정시": 3774,
    null: 292
  }
}
```

#### `curl` Example

```bash
curl "http://localhost:8000/stats"
```

---

### Running the Server

```bash
# Install dependencies
pip install fastapi "uvicorn[standard]"

# Start (with auto-reload for development)
python api.py

# Or directly via uvicorn
uvicorn api:app --reload --port 8000

# Interactive API docs
open http://localhost:8000/docs
```

---

## DB Schema

Database: `data/admission.db` (SQLite 3.45+)

### `admission_department`

Anchor records for each department. Represents a unique (year, university, campus, name) combination.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `year` | INTEGER | 모집요강 연도 (e.g. 2025) |
| `university` | TEXT | 대학교명 (e.g. `건국대학교`) |
| `campus` | TEXT | 캠퍼스명 (e.g. `ERICA`); empty string `''` if none |
| `track` | TEXT\|null | 계열 (e.g. `인문`, `자연`) |
| `name` | TEXT | 학과/학부명 (e.g. `컴퓨터공학부`) |

**UNIQUE constraint:** `(year, university, campus, name)`

**Indexes:** `idx_dept_univ (university)`, `idx_dept_year (year)`

**Note:** `campus` is stored as `''` (empty string) instead of NULL so the UNIQUE constraint works correctly (NULL != NULL in SQL).

---

### `admission_process`

Semi-structured 전형 (admission process) records extracted from 모집요강 PDFs.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `department_id` | INTEGER FK | References `admission_department(id)` |
| `process_name` | TEXT | 전형명 (e.g. `학생부교과`, `논술우수자`) |
| `process_type` | TEXT\|null | 전형 분류: `학생부교과`, `학생부종합`, `논술`, `실기`, `수능`, `기타` |
| `admission_type` | TEXT\|null | `수시` or `정시` |
| `quota` | INTEGER\|null | 모집인원 |
| `content` | TEXT\|null | Raw extracted text from the PDF table cell |
| `attributes` | TEXT | JSON object for freeform key-value data (default `{}`) |

**UNIQUE constraint:** `(department_id, process_name)`

**Indexes:** `idx_proc_dept`, `idx_proc_name`, `idx_proc_type`, `idx_proc_adm`

**FTS5 virtual table:** `admission_process_fts` — synced via triggers on INSERT/UPDATE/DELETE. Columns: `process_name`, `content_text`, `university`, `department_name`.

---

### `admission_result`

입시결과 records: competition rates, score cutoffs. Extracted from 입시결과 PDFs.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `department_id` | INTEGER FK | References `admission_department(id)` |
| `result_year` | INTEGER | 입시결과 연도 (e.g. 2025 = 2024년도 입시) |
| `process_name` | TEXT | 전형명 |
| `admission_type` | TEXT\|null | `수시` or `정시` |
| `score_type` | TEXT\|null | `등급`, `표준점수`, `환산점수`, `백분위` |
| `competition_rate` | REAL\|null | 경쟁률 (e.g. 12.4 means 12.4:1) |
| `average_score` | REAL\|null | 합격자 평균 점수/등급 |
| `cut_50` | REAL\|null | 50% 커트라인 |
| `cut_60` | REAL\|null | 60% 커트라인 |
| `cut_70` | REAL\|null | 70% 커트라인 (주로 사용됨) |
| `cut_80` | REAL\|null | 80% 커트라인 |
| `cut_85` | REAL\|null | 85% 커트라인 |
| `cut_90` | REAL\|null | 90% 커트라인 |
| `content` | TEXT\|null | Raw extracted text |
| `attributes` | TEXT | JSON object for freeform data (default `{}`) |

**UNIQUE constraint:** `(department_id, result_year, process_name)`

**Indexes:** `idx_res_dept`, `idx_res_name`, `idx_res_year`, `idx_res_adm`, `idx_res_score_type`, `idx_res_cut70`, `idx_res_avg`

**FTS5 virtual table:** `admission_result_fts` — same structure as `admission_process_fts`.

#### Score interpretation

| score_type | Scale | Better direction | Typical usage |
|------------|-------|-----------------|---------------|
| `등급` | 1.0–9.0 | lower = better | 수시 학생부 |
| `표준점수` | ~100–150 | higher = better | 정시 수능 |
| `백분위` | 0–100 | higher = better | 정시 수능 |
| `환산점수` | varies | higher = better | 학교별 환산 |

**커트라인 convention:** `cut_70 = 2.5` means "the 70th percentile admitted student had a grade of 2.5" (i.e., 30% of admitted students had 2.5 or better).

---

## Query Examples

### 1. Find all 수시 결과 for a specific university

```sql
SELECT d.name AS department, r.process_name, r.competition_rate,
       r.average_score, r.cut_70, r.result_year
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE d.university = '건국대학교'
  AND r.admission_type = '수시'
  AND r.score_type = '등급'
ORDER BY r.cut_70 ASC;
```

### 2. Find departments where grade 2.5 is 안정 (수시)

```sql
SELECT d.university, d.name AS department, r.process_name,
       r.cut_70, r.competition_rate, r.result_year
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.score_type = '등급'
  AND r.admission_type = '수시'
  AND r.cut_70 >= 2.5       -- student (2.5) is at or better than cut_70
ORDER BY r.cut_70 ASC       -- most competitive first
LIMIT 20;
```

### 3. Find departments within reach (도전) for grade 2.5

```sql
SELECT d.university, d.name AS department, r.process_name,
       r.cut_70, r.cut_80, r.result_year
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.score_type = '등급'
  AND r.admission_type = '수시'
  AND r.cut_70 IS NOT NULL
  AND r.cut_70 < 2.5         -- outside cut_70 (too competitive)
  AND r.cut_80 >= 2.5        -- but within cut_80
ORDER BY r.cut_70 DESC       -- closest reach first
LIMIT 20;
```

### 4. Search by department name keyword

```sql
SELECT d.university, d.name AS department, r.process_name,
       r.cut_70, r.result_year
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE d.name LIKE '%컴퓨터%'
  AND r.score_type = '등급'
ORDER BY d.university, r.cut_70 ASC;
```

### 5. Most competitive departments (highest competition_rate)

```sql
SELECT d.university, d.name AS department, r.process_name,
       r.competition_rate, r.cut_70, r.result_year
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.competition_rate IS NOT NULL
  AND r.result_year = 2025
ORDER BY r.competition_rate DESC
LIMIT 20;
```

### 6. Count results by university

```sql
SELECT d.university, COUNT(*) AS result_count,
       MIN(r.cut_70) AS best_cut70,
       MAX(r.cut_70) AS hardest_cut70
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.score_type = '등급'
GROUP BY d.university
ORDER BY result_count DESC;
```

### 7. Compare two years for the same department

```sql
SELECT r.result_year, r.process_name,
       r.competition_rate, r.cut_70, r.average_score
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE d.university = '연세대학교'
  AND d.name LIKE '%경영%'
  AND r.score_type = '등급'
ORDER BY r.result_year DESC, r.process_name;
```

### 8. Full-text search using FTS5

```sql
-- Search for "소프트웨어" in process names and content
SELECT t.process_name, d.university, d.name AS department
FROM admission_result_fts('"소프트웨어"') fts
JOIN admission_result t ON t.id = fts.rowid
JOIN admission_department d ON d.id = t.department_id
ORDER BY fts.rank
LIMIT 20;
```

### 9. Distribution of admission processes by type

```sql
SELECT process_type, COUNT(*) AS cnt,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM admission_process
GROUP BY process_type
ORDER BY cnt DESC;
```

### 10. Universities with 정시 표준점수 results (for 수능 matching)

```sql
SELECT d.university, d.name AS department, r.process_name,
       r.average_score, r.cut_70, r.competition_rate
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.score_type = '표준점수'
  AND r.admission_type = '정시'
ORDER BY r.average_score DESC
LIMIT 20;
```
