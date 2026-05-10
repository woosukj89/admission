# 입시결과 admission_type 오류 수정 계획

> 작성일: 2026-05-10
> 대상 DB: SQLite (local) + Supabase PostgreSQL
> 수정 대상 필드: `admission_result.admission_type`

---

## 1. 발견된 문제

~1,800개의 입시결과 레코드가 `admission_type = '정시'`로 잘못 저장됨.
실제로는 `학생부교과전형`, `학생부종합전형` 등 수시 전형임에도 불구하고.

---

## 2. 근본 원인

### 원인 1: 수시/정시 CDN 파일이 동일한 경우 (25개 대학)

일부 대학이 수시+정시 입시결과를 하나의 통합 PDF로 출판하고, CDN이 동일한 파일을 수시/정시 URL 두 곳에 모두 제공함.

UPSERT 동작:
- 수시 파일 처리 → `admission_type = '수시'` (정상)
- 정시 파일 처리 (동일 내용) → UNIQUE key `(department_id, result_year, process_name)` 충돌 → 덮어쓰기 → `'정시'`

확인된 25개 대학: 부산대학교, 건국대학교, 경북대학교, 고려대학교(세종), 덕성여자대학교, 동국대학교, 성균관대학교, 성공회대학교, 세명대학교, 연세대학교, 조선대학교, 차의과학대학교, 남서울대학교, 백석대학교, 국립한밭대학교, 우송대학교, 한양대학교(ERICA), 인천가톨릭대학교

### 원인 2: 수시 URL 없이 정시 URL로만 수집된 통합 파일

일부 대학은 CDN에서 정시 URL로만 파일을 제공 → 전체 레코드가 `admission_type = '정시'`로 저장.

| 대학교 | 레코드 수 |
|--------|----------|
| 국립한국교통대학교 | 123 |
| 목원대학교 | 105 |
| 한라대학교 | 103 |
| 경성대학교 | 99 |
| 동서대학교 | 91 |
| 대구대학교 | 71 |
| 국립군산대학교 | 64 |
| 경남대학교 | 61 |
| 상지대학교 | 51 |
| 동명대학교 | 42 |
| 신라대학교 | 39 |
| 기타 ~30개 대학 | - |

---

## 3. 적용된 수정 (완료)

### 코드 수정 (`src/extractors/extract_results_batch.py`)

1. **Page section detection**: `"정시 │ {번호}"` 형식 헤더 패턴 추가 (부산대 통합 문서 스타일)
2. **Duplicate file skip**: 수시/정시 JSON 파일이 바이트 단위로 동일한 경우 정시 파일 처리 건너뜀

### 데이터 수정 (SQLite 적용 완료)

SQL 파일: `migration/fix_admission_type_susi.sql`

- **Step 1** — 1,100건: 확인된 중복 파일 대학들, 대학명 기반 타겟 UPDATE
- **Step 2** — 1,134건: 전체 대상 안전 규칙 적용:
  ```sql
  (process_name LIKE '%학생부교과%' OR process_name LIKE '%학생부종합%')
  AND process_name NOT LIKE '%정시%'
  AND process_name NOT LIKE '%나군%'
  AND process_name NOT LIKE '%가군%'
  AND process_name NOT LIKE '%다군%'
  ```

### 수정하지 않은 항목 (정당한 정시 레코드)

- **서울대학교**: `"정시모집 기회균형특별전형_특수교육대상자"` — process_name에 '정시' 포함 → 안전 규칙으로 자동 제외
- **전남대학교**: `"나군학생부종합(조기취업형계약학과전형)"` — '나군' = 정시 나군 → 자동 제외

---

## 4. 잔여 조사 항목

### Gap A: `admission_type = '전형기간 자율'` 레코드

일부 파일에서 admission_type 컬럼 값이 비표준. 학생부교과/종합 전형이 이 타입으로 잘못 분류됐을 가능성 있음.

```sql
SELECT d.university, r.process_name, COUNT(*)
FROM admission_result r JOIN admission_department d ON d.id=r.department_id
WHERE r.admission_type = '전형기간 자율'
  AND (r.process_name LIKE '%학생부교과%' OR r.process_name LIKE '%학생부종합%')
GROUP BY d.university, r.process_name
ORDER BY COUNT(*) DESC;
```

### Gap B: `admission_type = NULL` 레코드 (~1,200건)

NULL 레코드는 grade-matching 쿼리에서 필터링되어 보이지 않음.
안전 규칙 적용 가능 여부 확인 필요.

### Gap C: 중복 파일 대학 DB 검증 미완료

국립한밭대학교, 우송대학교, 세명대학교는 중복 파일이 있으나 wrong-admission_type 쿼리에서 발견되지 않음 → 코드 수정 후 재추출 시 정상화됨. 현재 DB 상태 확인 필요.

### Gap D: 중복 파일 대학 중 이상 없는 것으로 확인된 대학

연세대학교, 성공회대학교, 남서울대학교, 백석대학교, 한양대학교(ERICA) — 중복 파일 목록에 있지만 wrong-admission_type 쿼리에서 발견 안 됨.
가능한 이유:
- (a) 알파벳 순으로 수시 파일이 마지막에 처리됨 (수시가 정시를 덮어씀)
- (b) 파일 내 page section detection이 이미 작동함

→ 각 대학 admission_result 레코드 분포 확인으로 검증 가능.

---

## 5. 작업 체크리스트

- [x] 코드 수정: page detection 패턴 추가
- [x] 코드 수정: 중복 파일 스킵 로직
- [x] 데이터 수정: 확인된 중복 파일 9개 대학 (1,100건, SQLite)
- [x] 데이터 수정: 전체 안전 규칙 적용 (1,134건, SQLite)
- [x] SQL 파일 생성: `migration/fix_admission_type_susi.sql`
- [x] GitHub 푸시
- [x] **Supabase에서 SQL 실행** (`migration/fix_admission_type_susi.sql`) — 완료
- [x] Gap A 조사: `전형기간 자율` 5,660건 → 합법적 전형 타입 (재직자/만학도). 수정 불필요.
- [x] Gap B 조사: `NULL` admission_type → 858건 수정 완료 (수시 660건, 정시 122건, 기타 76건). 45건 잔여 (편입학, 정원외 등 — NULL 유지 적합).
- [x] Gap C/D 검증: 결과 요약 — 성공회대/세명대/남서울대/백석대/ERICA 정상. 연세대 52건 (전형미상) 불명확하여 유지.
- [x] SQL 파일 생성: `migration/fix_null_and_grade_type.sql`
- [x] **Supabase에서 SQL 실행** (`migration/fix_null_and_grade_type.sql`) — 완료 (수시 26,517 / 정시 8,821 / 전형기간 자율 5,660 / NULL 75)
- [ ] 선택사항: 코드 수정 후 25개 대학 재추출 (현재 SQL 패치로 데이터 정상화됨)

## 6. Gap A 상세: 전형기간 자율

5,660건 / 80개 대학. 재직자전형, 만학도전형, 성인학습자전형 등이 포함.
이 전형들은 수능/내신 기반이 아닌 특수 대상자 전형으로, 현역 고3 학생에게는 해당 없음.
MCP 도구 쿼리에서 `admission_type='수시'` 필터와 별도로 `'전형기간 자율'`을 포함할 수 있으나,
일반 상담 목적에서는 제외가 적합. **현 상태 유지.**

---

## 6. 참고 파일

| 파일 | 설명 |
|------|------|
| `migration/fix_admission_type_susi.sql` | Supabase 적용용 SQL 패치 (2단계) |
| `src/extractors/extract_results_batch.py` | 추출기 코드 (page detection + duplicate skip 수정) |
| `admission_result` UNIQUE key | `(department_id, result_year, process_name)` — admission_type 미포함이 핵심 원인 |
