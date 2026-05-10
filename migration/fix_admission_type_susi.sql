-- Fix misclassified admission_type: 수시 전형 records stored as 정시
--
-- Root causes:
-- 1. CDN served identical combined PDFs under both 수시 and 정시 URLs.
--    When the 정시 file was processed later, it overwrote 수시 records via UPSERT.
--    Confirmed: 부산대학교, 건국대학교, 경북대학교, 고려대학교(세종),
--               덕성여자대학교, 동국대학교, 성균관대학교, 조선대학교, 차의과학대학교
--
-- 2. Some universities publish a single combined 수시+정시 PDF, which was downloaded
--    only under the 정시 URL, causing all records to be stored as '정시'.
--    (e.g. 국립한국교통대학교, 목원대학교, 경성대학교, 한라대학교, 동서대학교, etc.)
--
-- Safe fix rule: process_name contains '학생부교과' or '학생부종합' WITHOUT
--   explicit '정시', '나군', '가군', '다군' keywords → must be 수시.
-- Excludes: 서울대 "정시모집 기회균형특별전형", 전남대 "나군학생부종합" (legitimate 정시).
--
-- Run in Supabase SQL Editor.

-- Step 1: Fix confirmed duplicate-file universities (safe, targeted)
UPDATE admission_result r
SET
    admission_type = '수시',
    grade_type = CASE
        WHEN r.score_type = '등급' THEN '내신'
        WHEN r.score_type IS NOT NULL THEN r.score_type
        ELSE NULL
    END
FROM admission_department d
WHERE r.department_id = d.id
  AND d.university IN (
    '부산대학교',
    '건국대학교',
    '경북대학교',
    '고려대학교(세종)',
    '덕성여자대학교',
    '동국대학교',
    '성균관대학교',
    '조선대학교',
    '차의과학대학교'
  )
  AND r.admission_type = '정시'
  AND (
    r.process_name LIKE '%학생부교과%'
    OR r.process_name LIKE '%학생부종합%'
    OR r.process_name LIKE '%논술전형%'
  );

-- Step 2: Broad safe fix for all remaining universities
-- process_name with 학생부교과/종합 but no 정시/군 keywords = definitely 수시
UPDATE admission_result
SET
    admission_type = '수시',
    grade_type = CASE
        WHEN score_type = '등급' THEN '내신'
        WHEN score_type IS NOT NULL THEN score_type
        ELSE NULL
    END
WHERE admission_type = '정시'
  AND (process_name LIKE '%학생부교과%' OR process_name LIKE '%학생부종합%')
  AND process_name NOT LIKE '%정시%'
  AND process_name NOT LIKE '%나군%'
  AND process_name NOT LIKE '%가군%'
  AND process_name NOT LIKE '%다군%';

-- Verify: should return 0
SELECT COUNT(*) AS still_wrong_count
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE r.admission_type = '정시'
  AND (r.process_name LIKE '%학생부교과%' OR r.process_name LIKE '%학생부종합%')
  AND r.process_name NOT LIKE '%정시%'
  AND r.process_name NOT LIKE '%나군%'
  AND r.process_name NOT LIKE '%가군%'
  AND r.process_name NOT LIKE '%다군%';
