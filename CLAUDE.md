# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Windows + Python `.venv`. Always use:
- `.venv/Scripts/python.exe` (or activate with `.venv/Scripts/activate`)
- `PYTHONIOENCODING=utf-8` when running scripts that output Korean text

Install dependencies:
```bash
.venv/Scripts/pip install -e .
.venv/Scripts/pip install -r requirements.txt
```

## Common Commands

### Data Pipeline (run in order)
```bash
# 1. Download 모집요강 PDFs
python src/crawler/download_adiga.py

# 2. Extract PDFs → JSON
python src/extractors/extract_all_pdfs.py

# 3. JSON → SQLite (모집요강)
python src/extractors/extract_admission_batch.py

# 4. Download 입시결과 PDFs
python src/crawler/download_results.py [--google]

# 5. Extract 입시결과 PDFs → JSON
python src/extractors/extract_results_pdfs.py

# 6. JSON → SQLite (입시결과)
python src/extractors/extract_results_batch.py

# 7. Post-process fixes
python src/extractors/fix_kwangwoon_results.py [--dry-run]
python src/extractors/tag_special_admissions.py [--dry-run]
python src/extractors/update_전형요소.py [--dry-run]
```

### Running Servers
```bash
# MCP server (for Claude/AI tool use)
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe src/mcp_server.py

# Web client + REST API (serves frontend at http://localhost:8000)
# First time: copy .env.example to .env and fill in API keys
cp .env.example .env
uvicorn src.api:app --reload --port 8000
```

### Web Client Setup (first time)
1. Copy `.env.example` to `.env` and fill in:
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — from Google Cloud Console
     - Create OAuth2 Web App credential
     - Add authorized redirect URI: `http://localhost:8000/auth/callback`
   - `JWT_SECRET` — run: `python -c "import secrets; print(secrets.token_hex(32))"`
   - `GEMINI_API_KEY` — server-side key from https://aistudio.google.com/apikey
     - Use a dedicated key (not your personal one)
     - Free tier: 15 RPM / 1500 RPD; code adds 4s delay between tool-call iterations
2. Run: `uvicorn src.api:app --reload --port 8000`
3. Visit http://localhost:8000 → login with Google → start chatting immediately

### CLI
```bash
admission recommend "컴퓨터공학 수능 국수영탐 2233 내신 3.5등급"
admission crawl --university "서울대학교" --workers 10
admission search "입시결과"
admission stats
```

### Database Inspection
```bash
.venv/Scripts/python.exe -c "
import sqlite3; conn = sqlite3.connect('data/admission.db')
print(conn.execute('SELECT COUNT(*) FROM admission_department').fetchone())
print(conn.execute('SELECT COUNT(*) FROM admission_process').fetchone())
print(conn.execute('SELECT COUNT(*) FROM admission_result').fetchone())
"
```

## Architecture

### Data Flow
```
PDFs (data/raw/)
  → src/extractors/extract_all_pdfs.py → JSON (data/extracted/)
  → src/extractors/extract_admission_batch.py → admission_department + admission_process tables

PDFs/XLSX (data/results/)
  → src/extractors/extract_results_pdfs.py → JSON (data/results_extracted/)
  → src/extractors/extract_results_batch.py → admission_result table

data/admission.db ← queried by → src/mcp_server.py (MCP tools)
                              → src/api.py (REST API)
                              → src/recommend/ (LLM pipeline)
```

### Key Source Files
- **`src/storage/admission_store.py`** — Central SQLite data lake. 3 tables: `admission_department`, `admission_process`, `admission_result`. FTS5 full-text search. Auto-migrates schema on startup.
- **`src/mcp_server.py`** — FastMCP server exposing 8 tools to AI models. Primary external interface. Loads `data/university_meta.json` (region/tier) and `data/suneung_grade_table.json` (grade↔score) at startup.
- **`src/recommend/pipeline.py`** — 3-stage LLM pipeline: `query_parser.py` (parse student query) → `db_filter.py` (filter candidates from DB) → `analyzer.py` (Claude-powered ranking/explanation).
- **`src/parse_suneung_min.py`** — Parses 수능최저학력기준 strings into structured dicts. Used heavily by MCP tools.
- **`src/extractors/extract_results_batch.py`** — Most complex script (~110KB). Dispatch chain of special-format parsers for universities with non-standard PDF layouts (예수대, 서경대, 아주대, 충남대, 성공회대, 선문대, 한국성서대).
- **`src/extractors/backfill_suneung_min.py`** — Backfill framework for 수능최저학력기준. 42 university parsers. Run after `extract_admission_batch.py`.

### Database Schema
```sql
admission_department(id, year, university_name, campus, track, department_name, attributes JSON)
admission_process(id, dept_id FK, process_name, process_type, quota, content, attributes JSON)
admission_result(id, university_name, department_name, process_name, admission_type,
                 result_year, score_type, grade_type, competition_rate,
                 average_score, cut_50/60/70/80/85/90, process_id FK)
```
- `attributes` JSON column stores freeform data: `전형요소`, `수능최저`, `특수전형` flag, etc.
- `grade_type` distinguishes 내신 (수시) vs 수능등급/표준점수/백분위/환산점수 (정시)
- UNIQUE constraints normalize NULL → `''`

### MCP Tools (src/mcp_server.py)
`search_programs`, `match_by_grade`, `match_by_subjects`, `suggest_portfolio`, `get_process_detail`, `compare_universities`, `list_universities`, `search_fulltext`

The `suggest_portfolio` tool produces a 수시 6-card portfolio split into 안정/추천/도전 buckets, deduplicated by university.

### Score Type Logic
- 수시 → `score_type=등급`, `grade_type=내신`
- 정시 (수능) → `score_type` = 표준점수/백분위/환산점수, `grade_type=수능등급` (when 1-9 range)
- Post-hoc reclassification: 표준점수 records where all values < 200 are reclassified as 백분위

### University Metadata
`data/university_meta.json` maps 182 universities to `region` (서울/경기/…), `region_broad` (수도권/지방), `tier` (1–5). Used by `db_filter.py` for region filtering and by MCP tools for tier-based ranking.

## Important Constraints

- **Data accuracy is paramount** — no conflicting records for same university/campus/year/전형/학과. Verify before inserting.
- Quota cap: 1–500 per dept/전형 pair; skip if > 100K (overflow guard).
- Dept name max 50 chars (merged cells in PDFs produce garbage longer names).
- CDN URLs for 입시결과: use raw Korean strings — do NOT `urllib.parse.quote()` (causes double-encoding → 404).
- FTS5 JOIN syntax: `FROM fts_table(?) f` not `WHERE f MATCH ?`.
