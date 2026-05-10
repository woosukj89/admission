-- Fix misclassified admission_type: 수시 전형 records stored as 정시
-- Root cause: CDN served identical combined PDFs under both 수시 and 정시 URLs.
-- When the 정시 file (processed later) overwrote 수시 records, admission_type became '정시'.
-- Confirmed affected universities: those where 수시 and 정시 JSON files are byte-identical.
--
-- Applies to: 부산대학교, 건국대학교, 경북대학교, 고려대학교(세종), 덕성여자대학교,
--             동국대학교, 성균관대학교, 조선대학교, 차의과학대학교
--
-- Run in Supabase SQL Editor.

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

-- Verify: should return 0
SELECT COUNT(*) AS still_wrong
FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE d.university IN (
    '부산대학교','건국대학교','경북대학교','고려대학교(세종)',
    '덕성여자대학교','동국대학교','성균관대학교','조선대학교','차의과학대학교'
  )
  AND r.admission_type = '정시'
  AND (r.process_name LIKE '%학생부교과%' OR r.process_name LIKE '%학생부종합%');
