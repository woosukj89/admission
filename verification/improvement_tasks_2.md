# Improvement Task List (Round 2)
*Generated from `mcp_qa_report_2.md` — 2026-03-17*

---

## Summary

**Round 2 test**: 6 real student questions → 1/6 correct, 2/6 partially answered, 3/6 failed.
**Root causes** (in order of impact):
1. **수능최저 충족 여부 미체크** — 불가능한 전형을 "추천"으로 오답 제시
2. **특수전형 오염** — 기회균형/농어촌/특성화고 전형이 일반 결과 상위에 노출
3. **학과명 동의어 미적용** — "컴퓨터공학" 검색이 "소프트웨어학부" 못 찾음
4. **OCR 아티팩트 학과명** — "거머르트국저스무" 같은 깨진 이름이 결과에 노출
5. **정시 매칭 코어스** — 과목별 등급을 무시하고 단순 평균만 사용

---

## Track F — Data Filtering & Quality

### F1 · 특수전형 자동 필터 (기회균형/농어촌/특성화고/재직자)
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: S
**Blocks**: 모든 도구의 기본 결과 품질

**Problem**: `compare_universities`, `match_by_grade`, `match_by_subjects` 결과 상위에
기회균형Ⅱ(cut_70=1.40), 특성화고교(cut_70=1.20), 농어촌학생(cut_70=1.5) 등
일반 학생이 지원 불가능한 전형들이 포함됨. 이 전형들은 별도 자격 조건이 필요한
제한 전형이므로 일반 결과에서 기본 제외해야 함.

**특수전형 판별 패턴**:
```python
SPECIAL_ADMISSION_PATTERNS = [
    "기회균형", "농어촌", "특성화고", "재직자", "장애인", "사회배려",
    "저소득", "차상위", "기초생활", "특수교육대상자", "만학도",
    "다문화", "북한이탈", "탈북", "외국인", "편입",
]
```

**What to do**:
1. `admission_process.attributes["특수전형"] = true/false` 태그를 추가
2. `extract_admission_batch.py` 또는 배치 스크립트에서 process_name 기반으로 자동 태깅
3. `search_programs`, `match_by_grade`, `match_by_subjects`, `compare_universities` 모두에
   `exclude_special: bool = True` 파라미터 추가 (기본값 True — 특수전형 제외)
4. 기존 C2 `지역인재` 태깅처럼 별도 플래그로 관리

**Files**: 배치 태깅 스크립트 (new), `src/mcp_server.py`

---

### F2 · OCR 아티팩트 학과/전형명 필터링
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: 서강대 OCR 추출 결과에 "거머르트국저스무", "사릭과", "중국문화학과*" 같은
깨진/오염된 학과명이 DB에 저장되어 실제 추천 결과에 노출됨.

**판별 기준**:
- 한글 자모가 섞인 경우 (ㄱ, ㄴ, ㄷ 등 단독 자모)
- ASCII가 한글 자리에 섞인 경우
- 의미없는 문자열 (4글자 이상 식별 불가)
- 또는: 한글 유니코드 범위를 벗어난 이상한 코드포인트

**What to do**:
1. `src/mcp_server.py`의 결과 반환 시 `_is_valid_name(name: str) -> bool` 함수 추가
2. 깨진 이름을 가진 레코드는 결과에서 제외 (DB에서 삭제가 아닌 출력 필터)
3. 또는 별도 스크립트로 DB의 오염 레코드를 `NULL`로 업데이트
4. 향후 OCR 추출 시 `extract_ocr_results.py`에 동일 필터 적용

**Files**: `src/mcp_server.py`, `extract_ocr_results.py`

---

### F3 · 광운대 등 score_type=None 레코드 재처리
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: M

**Problem**: 광운대학교 65개 레코드 모두 `score_type=None`, `cut_70=None` — 경쟁률만 있고
등급 컷 없음. `match_by_grade`나 `compare_universities`가 이 데이터를 반환 못함.

**What to do**:
1. `data/results/광운대학교/` 파일 재검토 — 어떤 포맷인지 확인
2. 만약 컬럼 헤더 인식 실패로 score_type이 누락됐다면 `extract_results_batch.py` 수정
3. 대안: `data/results_extracted/광운대학교/` JSON에서 cut 데이터가 있는지 확인
4. 복구 불가 시 `admission_result`에서 score_type=NULL 레코드 삭제 후 경쟁률 별도 저장

**Files**: `extract_results_batch.py`, `data/results_extracted/광운대학교/`

---

## Track G — 답변 정확도 개선

### G1 · 수능최저 충족 여부 자동 체크 (🔴 최우선)
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: M

**Problem**: `match_by_grade`, `match_by_subjects`, `compare_universities` 결과에서
전형의 수능최저를 학생의 실제 수능 등급과 대조하지 않음.
→ 이 학생(국3+수2+영1+사4+과5)은 고려대 논술(수능최저: 4개합8이내) 충족 불가인데
"추천"으로 표시됨. 이것은 핵심 오답.

**학생 수능최저 충족 체크 로직**:
```python
def check_수능최저(student_grades: dict, 수능최저: dict) -> dict:
    """
    student_grades = {"국어": 3, "수학": 2, "영어": 1, "사탐": 4, "과탐": 5}
    수능최저 = {"있음": True, "조건": {"방식": "합", "개수": 4, "기준": 8}}
    Returns: {"충족": bool, "달성": X, "기준": Y, "부족": Z}
    """
```

**What to do**:
1. `match_by_grade` / `match_by_subjects` / `compare_universities`에
   `student_suneung: dict | None = None` 파라미터 추가
   - 예: `student_suneung={"국어": 3, "수학": 2, "영어": 1, "탐구": 4}`
2. 수능최저가 있는 전형에 대해 `check_수능최저()` 호출
3. 결과에 `수능최저_충족: true/false` 필드 추가
4. 충족 안 되는 전형은 `verdict`를 자동으로 `"도전(수능최저미충족)"` 또는 필터 제외
5. `get_process_detail`에도 동일 파라미터 추가

**Files**: `src/mcp_server.py`, `src/parse_수능최저.py` (check 함수 추가)

---

### G2 · get_process_detail 학과명 동의어 매칭
- [x] Done
**Priority**: 🔴 Critical
**Complexity**: S

**Problem**: `get_process_detail('국민대학교', '학생부종합전형', department='컴퓨터공학')`이
에러 반환. DB에 "컴퓨터공학" 학과가 없고 "소프트웨어학부"가 있어서 필터 매칭 실패.

**What to do**:
1. `department` 파라미터에 `_expand_keywords` 적용:
   - "컴퓨터공학" → ["컴퓨터", "소프트웨어", "IT공학", "정보공학"] 등으로 확장
2. 정확 매칭 실패 시 확장 키워드로 재시도
3. 매칭된 processes가 0일 때 에러 대신 "유사 학과 목록" 반환:
   ```json
   {
     "error": "exact match not found",
     "suggestions": [
       {"department": "소프트웨어학부", "process_name": "학생부종합전형", "cut_70": 2.66},
       {"department": "인공지능학부", "process_name": "학생부종합전형", "cut_70": 2.78}
     ]
   }
   ```
4. `search_programs`를 fallback으로 활용

**Files**: `src/mcp_server.py` (`get_process_detail` 함수)

---

### G3 · match_by_subjects에 track (계열) 필터 추가
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: Q2에서 "자연계열로" 필터 요청이 있는데 `match_by_subjects`에
`track` 파라미터가 없어서 자연/인문 혼재 결과가 반환됨.

**What to do**:
1. `match_by_subjects`에 `track: str | None = None` 파라미터 추가
2. `match_by_grade`도 동일하게 추가 (현재 없음)
3. `_matches_track()` 함수를 두 도구에서 공통 사용
4. expanded_kws 기반 dept 필터와 track 필터를 AND로 결합

**Files**: `src/mcp_server.py`

---

### G4 · 정시 과목별 등급 기반 매칭 개선
- [x] Done
**Priority**: 🟡 High
**Complexity**: L

**Problem**: `match_by_grade(grade=3.0, grade_type='수능등급')`는 과목 평균만 사용.
이 학생은 영어 1등급(매우 강점), 과학 5등급(매우 약점)인데 단순 평균 3.0으로만 매칭.
실제 정시에서는:
- 자연계: 수학/과탐 비중 높음 → 이 학생에 불리 (과학 5등급)
- 인문계: 국어/영어 비중 높음 → 이 학생에 유리 (영어 1등급)

**What to do**:
1. `match_by_subjects`에 `grade_type='수능등급'` 옵션 추가
   - 파라미터: `korean_suneung`, `math_suneung`, `english_suneung`, `science_suneung`, `social_suneung`
2. 각 대학의 정시 반영비율은 모집요강 content에서 파싱 필요
   - "국어 30% + 수학 40% + 영어 20% + 탐구 10%" 형식
   - 파싱 후 `attributes["정시반영비율"]`에 저장
3. 반영비율 데이터 없을 경우: 계열별 기본 비율 적용
   - 자연계 기본: 수학 40%, 탐구 30%, 국어 20%, 영어 10%
   - 인문계 기본: 국어 35%, 영어 30%, 수학 25%, 탐구 10%
4. effective_suneung_grade = Σ(과목 등급 × 반영비율)

**Files**: `src/mcp_server.py`, 새 파싱 함수 추가

---

### G5 · 데이터 미보유 대학 명시적 안내
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: S

**Problem**: 광운대처럼 DB에 cut score 데이터가 없는 대학에 대해
도구가 에러 없이 빈 결과나 경쟁률만 반환 — 사용자 입장에서 "데이터가 없다"는 사실을 모름.

**What to do**:
1. `get_process_detail` / `compare_universities`에서 결과 반환 시
   `"data_coverage": {"has_cut_scores": false, "has_competition_rate": true}` 필드 추가
2. cut score 없는 경우 `"data_note": "이 대학의 입결 컷 점수 데이터가 없습니다. 공식 입학처(링크) 또는 어디가(adiga.kr)를 참고하세요."` 안내
3. `list_universities` 결과에 `has_result_data: bool` 필드 추가
   - `admission_result`에 해당 대학의 cut_70 IS NOT NULL인 레코드가 있으면 true

**Files**: `src/mcp_server.py`

---

### G6 · 수시 6장 포트폴리오 최적화 도구
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: M

**Problem**: Q3 "인서울 전략 짜줘" 같은 질문에 단순 리스트만 반환.
수시는 6번의 지원 기회가 있으므로 안정/추천/도전 배분이 핵심.
현재 도구는 이 전략적 관점을 전혀 제공하지 않음.

**What to do**:
1. 새 MCP 도구 `suggest_portfolio` 추가:
   ```python
   def suggest_portfolio(
       grade: float,
       grade_type: str = "내신",
       region: str | None = None,
       major_keywords: list[str] = [],
       n_safe: int = 2,      # 안정 지원 수
       n_target: int = 3,    # 추천 지원 수
       n_reach: int = 1,     # 도전 지원 수
       student_suneung: dict | None = None,  # 수능최저 체크용
   ) -> dict:
       """Returns {"안정": [...2개], "추천": [...3개], "도전": [...1개]}"""
   ```
2. 반환 형식: 카테고리별로 묶어서 각 카테고리에서 학교 추천
3. 같은 대학 중복 방지 (6개 카드를 다른 대학에 배분)
4. 수능최저 충족 여부 자동 포함 (G1 연동)

**Files**: `src/mcp_server.py`

---

### G7 · 정시 표준점수/백분위 기반 인서울 매칭
- [x] Done
**Priority**: 🟡 High
**Complexity**: M

**Problem**: Q1/Q3에서 정시 인서울 대학이 전혀 나오지 않음.
이유: 서울 대학 정시는 거의 표준점수/백분위 기반이어서 등급 3.0 쿼리에 안 잡힘.
등급 3.0 → 표준점수 변환(E3 테이블 존재) 후 정시 매칭이 필요.

**What to do**:
1. `match_by_grade`에서 `grade_type='수능등급'` 선택 시:
   - 자동으로 E3 테이블(`suneung_grade_table.json`)을 참조해 표준점수 범위 계산
   - 예: 국어3 → 표준점수 87~100, 수학2 → 122~129, 영어1 → 원점수 90+ 등
   - 과목별 표준점수 범위를 합산해 정시 표준점수 DB와도 조회
2. `match_by_subjects(grade_type='수능등급')` 추가 시 각 과목 등급으로 표준점수 변환
3. 결과 통합: 등급 기준 + 표준점수 변환 기준 두 가지 결과 병합

**Files**: `src/mcp_server.py` (`grade_to_score_range` 함수 이미 존재, 활용 확대)

---

## Track H — 사용자 경험 개선

### H1 · 수능최저 원문 표시 개선
- [x] Done
**Priority**: 🟠 Medium
**Complexity**: S

**Problem**: 현재 `수능최저` 필드가 `{"있음": true, "조건": {"방식": "합", "개수": 3, "기준": 4}}`
형식으로 반환되어 AI가 해석은 할 수 있지만 사람이 읽기 어려움.
더 중요한 것은 학생의 수능 등급과 대조한 "충족 여부"가 없음.

**What to do**:
1. `수능최저` 필드에 `"원문_요약": "4개 합 8이내"` 형태의 사람이 읽기 쉬운 요약 추가
2. G1 구현 후 `"충족": true/false` 필드를 연동
3. 충족 불가 시: `"부족_설명": "현재 최선 조합 (1+2+3+4=10) > 기준 8"`

**Files**: `src/mcp_server.py`, `src/parse_수능최저.py`

---

### H2 · compare_universities에 학생 합격률 컬럼 추가
- [x] Done
**Priority**: 🟡 High
**Complexity**: S

**Problem**: Q6 "전국 컴퓨터공학 입결 순으로 나의 합격률 알려줘"에서
`compare_universities`는 cut_70을 반환하지만 학생 등급 대비 합격률(%)는 계산 안 함.

**What to do**:
1. `compare_universities`에 `student_grade: float | None = None` 파라미터 추가
2. 각 대학 결과에 `verdict`, `margin`, `acceptance_pct` 필드 추가
   - 기존 `_estimate_acceptance_pct()` 함수 재사용
3. `수능최저_충족`도 선택적으로 포함 (G1 연동 시)

**Files**: `src/mcp_server.py`

---

## Sprint 계획

### Sprint 1 (즉시 — 오답 방지) — 1~2일
- F1: 특수전형 필터 (가장 눈에 띄는 오염 제거)
- G1: 수능최저 충족 체크 (오답 추천 방지 — critical)
- G2: get_process_detail 학과명 동의어 매칭 (Q4 응답 실패 해결)

### Sprint 2 (품질 개선) — 2~3일
- F2: OCR 아티팩트 필터링
- G3: match_by_subjects track 필터
- H2: compare_universities 합격률 컬럼
- G5: 데이터 미보유 대학 안내

### Sprint 3 (고급 기능) — 3~5일
- G4: 정시 과목별 등급 매칭
- G6: 수시 포트폴리오 도구
- G7: 정시 표준점수 변환 매칭
- H1: 수능최저 원문 요약 개선

### Sprint 4 (데이터 확장) — 지속
- F3: 광운대 등 score_type=None 레코드 재처리
- 추가 대학 OCR 추출 (경희대 landscape, 가톨릭대 최고/평균/최저 컬럼 등)
