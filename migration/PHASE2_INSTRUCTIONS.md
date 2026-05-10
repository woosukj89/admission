# Phase 2 — Content Import Instructions

## Files
All batch CSVs are in `migration/csv/supabase/content_batches/`:
- `process_content_01.csv` (~29.6MB, rows 1–10,000)
- `process_content_02.csv` (~28.9MB, rows 10,001–20,000)
- `process_content_03.csv` (~28.5MB, rows 20,001–30,000)
- `process_content_04.csv` (~28.7MB, rows 30,001–40,000)
- `process_content_05.csv` (~27.4MB, rows 40,001–49,674)

Total: 49,674 rows, 143MB

## Step 1 — Create temp table in Supabase SQL Editor

```sql
CREATE TABLE IF NOT EXISTS process_content_temp (process_id INT, content TEXT);
```

## Step 2 — Import each batch via Table Editor

In Supabase dashboard:
1. Go to Table Editor → `process_content_temp`
2. Click "Insert" → "Import data from CSV"
3. Upload `process_content_01.csv`
4. Repeat for batches 02 through 05

## Step 3 — Apply content + rebuild search_vector

Run in SQL Editor after all 5 batches are imported:

```sql
UPDATE admission_process p
SET content = t.content,
    search_vector = to_tsvector('simple',
        COALESCE(p.process_name,'') || ' ' || COALESCE(t.content,''))
FROM process_content_temp t
WHERE p.id = t.process_id AND t.content != '';

DROP TABLE process_content_temp;
```

## Step 4 — Verify

```sql
SELECT
    COUNT(*) AS total,
    COUNT(NULLIF(content, '')) AS with_content,
    ROUND(AVG(length(content))) AS avg_content_len
FROM admission_process;
```

Expected: ~49,000 rows with content, avg 400–800 chars.

## Notes
- 758 rows are skipped (content = '') — these are in `admission_process_detail` with full content
- 509 rows extracted <100 chars — candidates for Phase 3 AI enhancement
- Phase 3: use Claude Pro sessions to process batches of ~500 short-content rows and generate SQL UPDATE patches
