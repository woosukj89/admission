# 개선 태스크 결과 보고서 (Round 5)
*2026-04-12 구현 및 테스트*

---

## 요약

모든 A1-A6 태스크가 이미 코드베이스에 구현되어 있음이 확인됨. 일부는 이전 라운드에서 이미 적용됨. 전체 구현 상태를 검증하고 테스트를 수행함.

---

## A1 — 경북대학교 수시 라벨 오류 수정

**상태**: 완료 (이전 라운드에서 이미 적용)

**확인 결과**:
- `fix_knu_labels.py` 이미 존재하며 정상 작동
- `fix_knu_labels.py` 실행 결과: "Found 0 경북대 records to fix" — 이전에 이미 수정됨
- 경북대 `admission_type='정시'` + `process_name LIKE '%학생부교과%' OR '%학생부종합%'` 레코드: **0건**

**현재 경북대 데이터 상태**:
- `grade_type='수능등급'` + `admission_type='수시'`: 90건 (학생부교과(교과우수자전형) — 수시 합격자의 수능등급 보고, 정상)
- `grade_type='내신'` + `admission_type='수시'`: 332건 (정상)
- A2 fix가 이 90건이 정시 쿼리에 혼입되지 않도록 방어

---

## A2 — match_by_grade grade_type='수능등급' 수시 데이터 제외

**상태**: 완료 (이미 구현됨)

**구현 위치**: `src/mcp_server.py` 라인 1207
```python
if db_grade_type == "수능등급" and r.get("admission_type") == "수시" and admission_type is None:
    continue
```

**검증 결과 (Q1)**:
- `match_by_grade(grade=3.0, grade_type='수능등급', limit=5)` → 5개 결과 모두 `admission_type='정시'`
- 수시 오염: 0/5 (통과)
- 현재 DB에 `grade_type='수능등급'` + `admission_type='수시'` 레코드: 716건 (경북대 90, 부산대 267, 조선대 등) — 모두 필터됨

---

## A3 — OCR 필터 개선 (_is_valid_dept_name)

**상태**: 완료 (이미 구현됨)

**구현 위치**: `src/mcp_server.py` 라인 265, 289
1. 후행 특수문자 제거: `name = re.sub(r'(\d+\))+$|[*†‡··∙•]+$', '', name).strip()` (라인 265)
2. bigram 검사 길이 기준: `if len(_name_no_bracket) >= 3` (라인 289, 이전 >= 4 에서 변경됨)
3. 3-5자 이름 추가 suffix 검사 (라인 323-325)

**남은 이슈**: `search_programs` 결과 출력(라인 660)에서 `p["department_name"]`을 직접 사용하므로 후행 artifact가 표시됨. `match_by_grade`(라인 1265)는 `dept_name_clean` 변수를 사용하여 정상 출력. `search_programs`에도 동일한 clean 처리 필요 (신규 이슈 N1 참조).

---

## A4 — match_by_grade university 파라미터

**상태**: 완료 (이미 구현됨)

**구현 위치**: `src/mcp_server.py` 라인 1021, 1092-1128
- 함수 시그니처: `university: str | None = None`
- university 지정 시 별도 SQL 경로로 해당 대학 전체 결과 조회

**검증 결과 (Q4)**:
- `match_by_grade(grade=2.8, grade_type='내신', university='서강대학교', limit=5)` → 5개 반환
- 모두 `서강대학교` 소속, 여러 학과(전자공학과, 생명과학과, 사회과학부, 화공생명공학과, 컴퓨터공학과)
- 1건이 아니라 여러 학과 반환: 통과

---

## A5 — compare_universities 빈 keyword 처리

**상태**: 완료 (이미 구현됨)

**구현 위치**: `src/mcp_server.py` 라인 1503-1505, 1511-1513
- 빈 keyword → 모든 학과 포함 (라인 1503-1505)
- `single_uni_mode`: keyword 없고 university 1개 → 모든 학과 반환 (라인 1511-1513)

---

## A6 — suggest_portfolio 지역 확장 fallback

**상태**: 완료 (이미 구현됨)

**구현 위치**: `src/mcp_server.py` 라인 1998-2040
- 확장 체인: 서울/인천/경기 → 수도권 → 전국(None)
- `note_region_expanded` 필드 추가

**검증 결과 (Portfolio)**:
- `suggest_portfolio(grade=2.8, region='서울', track='인문')`:
  - 서울 내 데이터 부족 → 전국 확장 발동
  - `note_region_expanded: "'서울' 내 데이터 부족 → 서울 → 전국으로 확장하여 추가 결과 포함"`
  - 안정 2개, 추천 3개, 도전 0개 (도전 데이터 부족 경고 표시)
  - 경고 메시지 정상 출력: "도전권 결과 1개 요청 중 0개만 찾음"

---

## M1 — 수능최저 백필 완료 상태

**상태**: 완료 (2026-04-12 이전 완료)

**확인 결과**:
- 충북대 `수능최저.있음=True` 레코드: 178건 (조건 및 원문 포함)
- 충북대 샘플: `학생부종합II` → `{"있음":true,"조건":{"방식":"합","개수":2,"기준":8},"원문":"반영영역 중 상위 2개 등급 합 8이내"}`
- `학생부교과` → `{"있음":true,"조건":{"방식":"합","개수":2,"기준":8},"원문":"반영영역 중 상위 2개 등급 합 8이내"}`
- 백필 완료로 get_process_detail에서 수능최저 조건 정상 반환

---

## 테스트 결과

### Q1: match_by_grade(grade=3.0, grade_type='수능등급', limit=5)

| 대학 | 학과 | 전형 | admission_type | cut_70 |
|------|------|------|----------------|--------|
| 조선대학교 | 식품영양학과 | 일반전형 | 정시 | 2.09 |
| 조선대학교 | K-컬처공연.기획학과 | 지역인재전형 | 정시 | 2.18 |
| 조선대학교 | 경찰행정학과 | 일반전형 | 정시 | 2.69 |
| 조선대학교 | 간호학과 | 지역인재전형 | 정시 | 3.0 |
| 조선대학교 | 간호학과 | 일반학생전형 | 정시 | 3.25 |

**평가**: 통과. 수시 데이터 혼입 0건. 모두 정시 레코드. (조선대학교만 나오는 것은 데이터 커버리지 한계 — 정시 수능등급 데이터가 있는 대학이 제한적)

### Q2: match_by_subjects(korean=3, math=2, english=3, science=4, 자연계, limit=10)

결과 10개 반환, 모두 `admission_type='수시'`, `grade_type='내신'`. 전남대, 경북대, 충북대 등 지방거점 국립대 위주 — 수시 내신 2.8 수준에서 합리적인 결과.

**평가**: 통과. 결과 합리적, 자연계 학과 필터 정상 작동.

### Q3: get_process_detail(university='충북대학교', process_name='학생부교과(학생부교과전형)')

- 전형 found: 없음 (admission_process에 해당 전형 없음)
- fallback: `departments` 응답 반환
- 충북대 의예과 cut_70=1.09, 수의예과 cut_70=1.14 등 수시 내신 데이터 정상 반환
- 수능최저: `학생부교과` 전형에 `{"있음":true,"조건":{"방식":"합","개수":2,"기준":8},"원문":"반영영역 중 상위 2개 등급 합 8이내"}` 백필 확인
- `get_process_detail` 응답에 수능최저 미반환: process가 없는 경우 fallback 경로에서 attributes가 없어 수능최저가 표시되지 않음 (신규 이슈 N2 참조)

**평가**: 부분 통과. 입시결과 데이터는 정상이나 fallback 경로에서 수능최저 미반환.

### Q4: match_by_grade(grade=2.8, grade_type='내신', university='서강대학교', limit=5)

- 5개 학과 반환 (전자공학과, 생명과학과, 사회과학부, 화공생명공학과, 컴퓨터공학과)
- 모두 `학생부교과(지역균형)` 전형, cut_70 = 1.39~1.44 (안정 기준 대비 도전권)
- A4 university 파라미터 정상 작동

**평가**: 통과. 여러 학과 반환 확인.

### Q5: get_process_detail(university='광운대학교', process_name='학생부종합(광운참빛인재전형-서류형)')

- `matched_dept_count: 32`
- 2024/2025년 데이터 포함
- 컴퓨터정보공학부: 2025 cut_70=2.53, 2024 cut_70=2.67
- 전자융합공학과: 2025 cut_70=2.48, 2024 cut_70=2.88

**평가**: 통과. 광운대 `fix_kwangwoon_results.py`로 삽입된 데이터 정상 반환.

### Q6: search_programs(keyword='컴퓨터공학', exclude_regional=True, limit=10)

10개 반환, 고려대(사이버국방학과, 인공지능학과, 컴퓨터학과) 위주. OCR 가비지 없음.

**평가**: 통과. 10개 반환 확인.

### Q7: search_programs(keyword='서강대학교', limit=10) / search_programs(university='서강대학교')

- 결과 50개 반환
- OCR artifact 후행문자 (`*`, `3)`, `2)`, `1)`) 가 dept 이름에 잔존:
  - `AI기반자유전공학부3)`, `물리학과*`, `수학과*`, `시스템반도체공학과2)`, `중국문화학과*` 등 24건
- `_is_valid_dept_name` 필터는 내부적으로 이를 처리하나 출력 시 미제거

**평가**: 부분 통과. 가비지 학과명은 없으나 footnote/artifact 후행 문자가 출력에 잔존.

### Portfolio: suggest_portfolio(grade=2.8, region='서울', track='인문')

- 서울 내 데이터 부족 → 전국 확장 발동 (A6 정상 작동)
- 안정 2개 (국민대학교 영어영문학부, 인하대학교 행정학과)
- 추천 3개 (충북대, 경북대, 전남대 — 서울 아닌 지방 대학)
- 도전 0개 (경고 출력)
- `note_region_expanded` 필드 정상 포함

**평가**: 통과 (A6 fix 작동 확인). 다만 서울/인문계열 내신 데이터 부족으로 추천 결과가 지방 대학 위주 — M2 데이터 수집 필요.

---

## 신규 발견 이슈

### N1 (낮은 우선순위) — search_programs 출력에 OCR artifact 후행문자 잔존

**근거**: `search_programs` 결과 `department` 필드에 `*`, `3)`, `2)`, `1)` 등 각주 마커가 잔존.
- `match_by_grade`는 이미 `dept_name_clean = re.sub(...)` 처리함
- `search_programs`는 `p["department_name"]` 직접 사용 (라인 660)

**수정 방법**: `search_programs` 라인 660:
```python
"department": re.sub(r'(\d+\))+$|[*†‡··∙•]+$', '', p["department_name"]).strip(),
```

**우선순위**: 낮음 (기능 결함 아님, 표시 개선)

---

### N2 (중간 우선순위) — get_process_detail fallback 경로에서 수능최저 미반환

**근거**: `get_process_detail(university='충북대학교', ...)` 호출 시 admission_process에 전형이 없어 fallback(admission_result 기반) 응답이 반환됨. 이 경우 `attributes`가 없으므로 수능최저 조건이 응답에 포함되지 않음.

**현재 상태**: fallback 응답 형식에 `attributes` 필드 없음 → 수능최저 정보 누락

**수정 방법**: fallback 경로에서 대학명+전형명으로 `_lookup_suneung_min_bulk()` 호출하여 수능최저 추가 조회

**우선순위**: 중간 (수능최저 정보 접근성 향상)

---

### N3 (낮은 우선순위) — 정시 수능등급 결과 대학 다양성 부족

**근거**: `match_by_grade(grade=3.0, grade_type='수능등급', limit=5)` 결과가 조선대학교 5개로 독점. 정시 수능등급 데이터가 있는 대학이 매우 제한적.

**현황**: 정시 수능등급 데이터 보유 대학 극소수. 대부분 대학이 정시에서 수능 표준점수/백분위 사용.

**권장**: 정시 쿼리 시 수능등급 데이터 없으면 백분위 변환 병행 안내 추가

**우선순위**: 낮음 (데이터 커버리지 한계)

---

## 우선순위 테이블

| ID | 이슈 | 우선순위 | 종류 |
|----|------|----------|------|
| N1 | search_programs 출력 artifact 후행문자 | 낮음 | AUTO |
| N2 | get_process_detail fallback 수능최저 미반환 | 중간 | AUTO |
| N3 | 정시 수능등급 결과 대학 다양성 부족 | 낮음 | MANUAL (데이터) |
| M2 | 서울 상위권 수시 내신 데이터 수집 | 높음 | MANUAL |
| M3 | 서울 인문계열 수시 내신 데이터 부족 | 높음 | MANUAL |

---

## 전체 상태 요약

| 태스크 | 상태 | 비고 |
|--------|------|------|
| A1 경북대 라벨 수정 | 완료 | 0건 추가 수정 필요 |
| A2 수능등급 수시 필터 | 완료 | 716건 정상 필터링 |
| A3 OCR 필터 개선 | 완료 | search_programs 출력 미처리 (N1) |
| A4 university 파라미터 | 완료 | 서강대 5개 학과 정상 반환 |
| A5 compare 빈 keyword | 완료 | single_uni_mode 정상 |
| A6 portfolio 지역 확장 | 완료 | note_region_expanded 정상 |
| M1 수능최저 백필 | 완료 | 충북대 178건 포함 |
| M2 서울 상위권 수시 데이터 | 미완 | 연세대/한양대/성균관대/이화여대 |

**데이터 신뢰도**: 전반적으로 양호. 정시 수능등급 데이터 커버리지가 낮음(조선대 위주)은 데이터 수집 한계.
