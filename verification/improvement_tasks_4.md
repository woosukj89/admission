# 개선 태스크 목록 (Round 4)
*2026-03-29 재검증 테스트 기반*

---

## 🤖 AUTO — Claude가 직접 구현 가능

### A1. 경북대학교 수시 라벨 오류 수정

**근거**: 검증 테스트 — `match_by_grade(grade=3.0, grade_type='수능등급')` 결과에 경북대 `학생부교과전형`, `학생부종합전형` 레코드가 `admission_type='정시'`, `grade_type='수능등급'`으로 잘못 태깅되어 나타남. 부산대와 동일한 패턴.

```sql
-- 확인 쿼리
SELECT COUNT(*) FROM admission_result r
JOIN admission_department d ON d.id = r.department_id
WHERE d.university = '경북대학교'
  AND r.admission_type = '정시'
  AND (r.process_name LIKE '%학생부%')
```

**수정 내용**:
- `fix_knu_labels.py` 스크립트 작성 (fix_busan_labels.py 패턴 동일)
- 경북대 `admission_type='정시'` AND `process_name LIKE '%학생부교과%' OR '%학생부종합%'` → `admission_type='수시'`로 수정
- 적용 후 검증

**기대 효과**: 수능등급 정시 쿼리에서 경북대 수시 데이터 제거

---

### A2. match_by_grade grade_type='수능등급' 쿼리에서 수시 데이터 제외

**근거**: `grade_type='수능등급'`이면서 `admission_type='수시'`인 레코드 267건(부산대)이 정시 쿼리에 혼입됨. `match_by_grade(grade=3.0, grade_type='수능등급')` 결과 상위권에 부산대 학생부종합전형이 다수 등장.

**원인**: `find_results_by_score(grade_type='수능등급')`가 admission_type 관계없이 grade_type만 필터링.

**수정 내용**:
- `src/mcp_server.py` — `match_by_grade()` 내부에서 `grade_type='수능등급'` 시 `admission_type='정시'` 추가 필터:
  ```python
  if grade_type == "수능등급":
      results = [r for r in results if r.get("admission_type") == "정시"]
  ```
- 또는 `find_results_by_score()`에 `admission_type` 파라미터 추가

**기대 효과**: 정시 수능등급 쿼리 정확도 향상. 부산대 수시 데이터가 정시 결과에 혼입되지 않음.

---

### A3. 서강대 OCR 학과명 잔류 이슈 — 필터 보완

**근거**: `_is_valid_dept_name()` 필터가 다음 케이스를 통과시킴:
- `"화아가"` (3자, 길이 미달로 bigram 검사 면제)
- `"경제하가"` (4자, `"경제"` bigram이 valid로 판단 → 통과)
- `"중국문화학과*"` (별표 아티팩트 포함)

**수정 내용**:
1. 길이 기준을 `>= 4`에서 `>= 3`으로 낮춰 3자 OCR 쓰레기도 검사
2. 학과명 끝의 특수문자 제거: `name = re.sub(r'[*†‡·]+$', '', name.strip())`
3. bigram 검사: 4자 이상 이름에서 valid bigram이 이름의 25% 미만이면 추가 의심 플래그 (예: "경제하가" — 4 bigrams: "경제","제하","하가" 중 1개만 valid = 33% → 통과. 임계값 조정 필요)

**기대 효과**: "화아가", "경제하가" 등 단어 단계 OCR 가비지 필터링

---

### A4. match_by_grade에 `university` 파라미터 추가

**근거**: Q4 "서강대학교를 가고 싶어, 가능성 높은 학과 5개" 쿼리를 처리할 도구가 없음. `compare_universities`는 빈 keyword를 받으면 1개만 반환하는 버그 있음. `match_by_grade`에 university 필터가 없어 특정 대학 내 학과 검색 불가.

**수정 내용**:
- `src/mcp_server.py` — `match_by_grade()` 함수 시그니처에 `university: str | None = None` 추가
- DB 쿼리 WHERE절에 `d.university LIKE ?` 조건 추가
- docstring 업데이트

**기대 효과**: "○○대학교 어떤 학과든 내 성적으로 갈 수 있는 곳" 질문 처리 가능

---

### A5. compare_universities 빈 keyword 처리 개선

**근거**: `compare_universities('', universities=['서강대학교'], ...)` 호출 시 1개만 반환 (빈 keyword가 모든 학과명에 매칭되어야 하지만 필터 로직 오류).

**원인**: `expanded_lower = ['']`이 되어 `'' in dept_name`이 항상 True지만 university 필터 후 `best` dict에서 대학당 1개만 유지하는 로직과 충돌. 실제로는 서강대 학과가 1개만 있는 게 아니라 필터에서 잘못 처리됨.

**수정 내용**:
- `compare_universities()`에서 `department_keyword`가 빈 문자열이면 모든 학과 포함
- `universities` 리스트가 지정된 경우 keyword 없이도 해당 대학 전체 학과 반환
- 대학별 best 선택 로직 개선: cut_70 기준으로 admission_type별 분리해서 취합

**기대 효과**: 단일 대학 전체 학과 입결 조회 정상 작동

---

### A6. suggest_portfolio 추천 버킷 부족 시 전국 확장 fallback

**근거**: `suggest_portfolio(grade=2.8, region='서울', track='인문')` 결과 추천 버킷 1건, 도전 버킷 0건. 서울에 내신 데이터가 있는 인문계열 학교가 극히 적기 때문. 사용자에게 "서울에서 정보 없음"을 고지하고 수도권/전국 확장 안내 필요.

**수정 내용**:
- `suggest_portfolio()` — 안정/추천/도전 각 버킷이 목표 수 미달 시:
  1. region을 `"수도권"` → `None`으로 단계적 확장 재시도
  2. 확장 시 `note_region_expanded: true` 필드 및 설명 메시지 추가
  ```python
  result["note_region_expanded"] = f"'{region}' 내 데이터 부족 → 전국 확장하여 {len(추천)}개 추천"
  ```

**기대 효과**: 지역 필터로 인한 빈 포트폴리오 방지. 데이터 공백을 사용자에게 투명하게 고지.

---

## 🙋 MANUAL — 사람의 직접 확인/작업 필요

### M1. 수능최저 미파싱 원문 복원 (extract_admission_batch.py 수정)

**근거**: 충북대 등 37.6%(5,242건) 전형에서 수능최저 조건이 `{있음: True}`만 기록되어 파싱 불가. 원문 텍스트가 DB에 없어서 재파싱도 불가.

**확인 및 작업**:
1. 아래로 미파싱 대학 목록 확인:
   ```python
   import sqlite3, json
   conn = sqlite3.connect('data/admission.db')
   rows = conn.execute('''
     SELECT university, COUNT(*) FROM admission_process
     WHERE json_extract(attributes, '$.수능최저.있음') = 1
       AND json_extract(attributes, '$.수능최저.조건') IS NULL
     GROUP BY university ORDER BY COUNT(*) DESC LIMIT 15
   ''').fetchall()
   for r in rows: print(r)
   ```
2. `extract_admission_batch.py`에서 수능최저 원문 텍스트를 `attributes.수능최저.원문`에 저장하도록 수정
3. 대상 대학 모집요강 재추출 (batch 재실행)
4. 재추출 후 `parse_suneung_min.py`로 재파싱

**예상 소요**: 1시간 (코딩 30분 + 재추출 30분)

---

### M2. 서울 상위권 대학 수시 내신 데이터 수집

**근거**: 연세대, 한양대, 성균관대, 이화여대 수시 내신 데이터 없음. 서울 내신 2.8 쿼리가 서강대 위주로만 나옴. 주요 수요 학교들의 데이터 공백.

**확인 필요**:
1. CDN/adiga.kr에서 해당 학교 입시결과 PDF 재확인 (2024/2025)
2. 각 학교 입학처에서 직접 내신 등급 컷 수집
3. adiga.kr 입시결과 섹션에서 다운로드 시도:
   ```
   연세대학교, 한양대학교, 성균관대학교, 이화여자대학교
   ```
4. 수동 수집 후 CSV→DB 삽입 스크립트 작성

**예상 소요**: 2시간 (자료 수집) + 30분 (Claude 삽입)

---

### M3. 2026학년도 입시결과 수집 (타이밍 체크)

**근거**: 현재 DB는 2024(11,625건)/2025(7,343건) 위주. 2026 데이터는 3개 대학 121건만 존재. 2026-03-29 기준 CDN 대부분 미게시.

**확인 방법**:
```bash
# CDN 게시 여부 확인 (경희대 예시):
curl -I "https://cdn013.negagea.net/dgsmidc/omr/seoul/web/univ_info2025/경희대학교/경희대학교_2026학년도_수시입시결과.pdf"
```

**작업**:
1. 월 1회 CDN 게시 여부 확인 (4월 이후 주요 대학 순차 게시 예상)
2. 게시 확인 후 `download_results.py`에서 YEAR=2026으로 실행
3. 우선순위: 경희대, 중앙대, 한국외대, 한양대, 이화여대

---

## 우선순위 정리

| 순위 | 태스크 | 유형 | 임팩트 | 난이도 |
|------|--------|------|--------|--------|
| 1 | **A2** 수능등급 쿼리 수시 데이터 제외 | AUTO | 버그 수정 (Q1 오염) | 낮음 |
| 2 | **A1** 경북대 수시 라벨 오류 수정 | AUTO | 버그 수정 (Q1/Q2) | 낮음 |
| 3 | **A4** match_by_grade university 파라미터 | AUTO | Q4/Q5 기능 추가 | 낮음 |
| 4 | **A5** compare_universities 빈 keyword | AUTO | Q4 버그 수정 | 중간 |
| 5 | **A3** 서강대 OCR 필터 보완 | AUTO | Q7 품질 | 중간 |
| 6 | **A6** suggest_portfolio 지역 확장 fallback | AUTO | Q6 UX | 중간 |
| 7 | **M1** 수능최저 원문 복원 | **MANUAL** | Q3 충족판별 | 높음 |
| 8 | **M2** 서울 상위권 수시 내신 데이터 | **MANUAL** | 데이터 공백 | 높음 |
| 9 | **M3** 2026 입시결과 수집 | **MANUAL** | 최신화 | 높음 |

---

## 테스트 결과 요약 (2026-03-29)

| 질문 | 결과 | 주요 이슈 |
|------|------|-----------|
| Q1: 정시 수능등급 3.0 상위 5 | 부산대/경북대 수시 데이터 혼입 | A1/A2 필요 |
| Q2: 수시+정시 자연 상위 10 | 부산대/경북대 혼입 동일 | A1/A2 필요 |
| Q3: 충북대 수능최저 | 37.6% 미파싱 — 충족 판별 불가 | M1 필요 |
| Q4: 서강대 합격 학과 5개 | compare_universities 빈 keyword → 1건 | A4/A5 필요 |
| Q5: 광운대 상세 (get_process_detail) | 정상 작동 (지역균형 위주 안내 포함) | 양호 |
| Q6: 컴퓨터공학 비교 exclude_regional | 수정 후 정상 (10개 반환) | 수정 완료 |
| Q7: 서강대 서류 준비 | OCR 가비지 학과명 2건 통과 | A3 필요 |
| Portfolio 서울 인문 2.8 | 추천 1건, 도전 0건 (데이터 공백) | A6/M2 필요 |
