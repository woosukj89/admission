-- Fix NULL admission_type and invalid grade_type records
-- Run in Supabase SQL Editor AFTER fix_admission_type_susi.sql
--
-- Fixes:
-- 1. NULL admission_type where process name clearly indicates 수시 → '수시'
-- 2. NULL admission_type where score_type indicates 정시 → '정시'
-- 3. grade_type = '등급' (invalid legacy value) → '내신' or '수능등급'
-- 4. 수능_ prefixed process names → '정시'

-- Step 1: Clear 학생부교과/종합/논술 NULLs → 수시
UPDATE admission_result
SET
    admission_type = '수시',
    grade_type = CASE WHEN score_type = '등급' THEN '내신' ELSE score_type END
WHERE admission_type IS NULL
  AND (process_name LIKE '%학생부교과%' OR process_name LIKE '%학생부종합%' OR process_name LIKE '%논술%')
  AND process_name NOT LIKE '%정시%' AND process_name NOT LIKE '%나군%' AND process_name NOT LIKE '%가군%';

-- Step 2: 교과/종합류 with 등급 score → 수시
UPDATE admission_result
SET admission_type = '수시', grade_type = '내신'
WHERE admission_type IS NULL
  AND score_type = '등급'
  AND (process_name LIKE '%교과%' OR process_name LIKE '%종합%' OR process_name LIKE '%지역인재%'
       OR process_name LIKE '%일반학생%' OR process_name LIKE '%일반전형%')
  AND process_name NOT LIKE '%정시%' AND process_name NOT LIKE '%나군%' AND process_name NOT LIKE '%가군%'
  AND process_name NOT LIKE '%수능%';

-- Step 3: Score type-based classification for remaining NULLs
UPDATE admission_result
SET admission_type = '정시', grade_type = score_type
WHERE admission_type IS NULL
  AND score_type IN ('백분위', '표준점수', '환산점수');

-- Step 4: 수능_ prefixed process → 정시
UPDATE admission_result
SET admission_type = '정시'
WHERE admission_type IS NULL AND process_name LIKE '수능_%';

-- Step 5: Null + 수시-style process names (no score_type)
UPDATE admission_result
SET admission_type = '수시'
WHERE admission_type IS NULL AND score_type IS NULL
  AND (process_name LIKE '%일반학생%' OR process_name LIKE '%지역인재%' OR process_name LIKE '%농어촌%'
       OR process_name LIKE '%교과일반%' OR process_name LIKE '%기초생활%' OR process_name LIKE '%사회다양%'
       OR process_name LIKE '%특성화고%' OR process_name LIKE '%실기전형%' OR process_name LIKE '%실기우수%'
       OR process_name LIKE '%지역기회균형%' OR process_name LIKE '%지역균형인재%'
       OR process_name LIKE '%특수교육대상자%' OR process_name LIKE '%특기자%')
  AND process_name NOT LIKE '%정시%' AND process_name NOT LIKE '%수능%';

-- Step 6: Remaining NULL + score_type=등급 (not 수능) → 수시
UPDATE admission_result
SET admission_type = '수시', grade_type = '내신'
WHERE admission_type IS NULL AND score_type = '등급'
  AND process_name NOT LIKE '%수능%' AND process_name NOT LIKE '%정시%';

-- Step 7: Fix invalid grade_type = '등급' (legacy value before grade_type computation)
UPDATE admission_result SET grade_type = '내신'
WHERE grade_type = '등급' AND (admission_type = '수시' OR admission_type IS NULL) AND score_type = '등급';

UPDATE admission_result SET grade_type = '수능등급'
WHERE grade_type = '등급' AND admission_type = '정시' AND score_type = '등급';

-- Verify: check final NULL count (should be ~45: 편입학, 정원외, 만학도 etc.)
SELECT admission_type, COUNT(*) AS cnt
FROM admission_result
GROUP BY admission_type
ORDER BY cnt DESC;
