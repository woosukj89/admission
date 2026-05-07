# 개선 태스크 목록 (Round 3)
*mcp_qa_report_3.md 기반 | 2026-03-17*

---

## 🤖 AUTO — Claude가 직접 구현 가능

### A1. department SYNONYMS 확장 (get_process_detail 매칭 개선)

**근거**: Q4 — `get_process_detail('국민대학교', '학생부종합전형', department='컴퓨터공학')` 가 소프트웨어학부 1건만 반환. 국민대 "인공지능학부"(cut_70=2.78), "AI빅데이터융합경영학과[자연]"(2.78) 미매칭.

**원인**: `_expand_keywords(['컴퓨터공학'])` → `['컴퓨터','소프트웨어','정보공학','전산','IT공학']`에 'AI', '인공지능', '빅데이터', 'SW', '데이터사이언스' 없음.

**수정 내용**:
- `src/mcp_server.py` — SYNONYMS 또는 CATEGORY_KEYWORDS IT/컴퓨터 클러스터에 추가:
  `'AI', '인공지능', '빅데이터', 'SW', '데이터사이언스', '정보통신', '사이버보안'`
- G2 폴백에서 확장 키워드 리스트가 department 필터에 올바르게 적용되는지 검증

**기대 효과**: 국민대 컴퓨터 관련 4개 학과 모두 매칭

---

### A2. 지역균형/지역인재 전형 제한 표시

**근거**: Q6 — `compare_universities('컴퓨터', exclude_special=True)` 결과에 서강대 지역균형(1위, cut_70=1.44), 건국대 KU지역균형(3위, 1.75), 인하대 지역균형(7위, 2.44), 전남대 지역인재(12위, 2.65) 포함. 이 전형들은 지역 고교 출신만 지원 가능 또는 서울 고교 출신 제한.

**수정 내용**:
- `src/mcp_server.py` — 결과 enrichment 단계에서 process_name에 `지역균형`, `지역인재`, `지방인재` 포함 시 `is_regional_restricted: true` 필드 추가
- `match_by_grade`, `match_by_subjects`, `compare_universities`에 `exclude_regional: bool = False` 파라미터 추가
- True 시 is_regional_restricted 항목 결과에서 제외

**기대 효과**: exclude_regional=True 사용 시 실질 일반 전형 순위 정확도 향상

---

### A3. compare_universities 반환 타입 수정 (list → dict)

**근거**: 코드 버그 — `compare_universities(...)` 가 bare `list` 반환. 호출 측에서 `result.get('rows')` 시 AttributeError 발생.

**수정 내용**:
- `src/mcp_server.py` — `compare_universities()` 반환을 dict로 감싸기:
  ```python
  return {
      "keyword": dept_keyword,
      "total": len(rows),
      "grade_type": grade_type,
      "student_grade": student_grade,
      "rows": rows
  }
  ```

**기대 효과**: 타입 일관성 확보, 호출 측 AttributeError 방지

---

### A4. OCR 아티팩트 휴리스틱 필터 강화

**근거**: Q3 — 서강대 "거머르트국저스무", "사릭과", "중교사과", "급도더인국릭부" 등 OCR 오류 학과명이 F2 필터를 통과. 현재 필터는 Hangul 자모(U+3131–U+3163)만 탐지하며 정상 Hangul 음절 조합으로 된 nonsense는 못 잡음.

**수정 내용**:
- `src/mcp_server.py` — `_is_valid_dept_name()` 에 추가 휴리스틱:
  1. 학과명에 일반 명사 종결어미(`과`, `학과`, `학부`, `전공`, `대학`, `부`) 포함 여부 확인 — 없으면 의심
  2. 길이 > 12자이고 공백 없고 자연스러운 명사 경계 없으면 플래그
  3. 알려진 OCR 오류 패턴 명시적 blocklist 추가 (서강대 대상)

**기대 효과**: 서강대 OCR 오류 학과명 결과에서 제외

---

### A5. get_process_detail 학과별 연도 그룹핑 + 트렌드 추가

**근거**: Q4/Q5 — 결과가 연도 순 flat list로 반환되어 연도별 비교가 어려움. 광운대 건축공학과: 2024 cut_70=2.89 → 2025 cut_70=4.36 (대폭 상승)처럼 중요한 트렌드가 묻힘.

**수정 내용**:
- `src/mcp_server.py` — `get_process_detail()` 결과를 학과별로 그룹화:
  ```json
  {
    "department": "소프트웨어학부",
    "years": [
      {"result_year": 2025, "cut_70": 2.66, ...},
      {"result_year": 2024, "cut_70": 2.71, ...}
    ],
    "trend_direction": "improving"
  }
  ```
- `trend_direction`: 최신 vs 이전년도 cut_70 비교 → `"harder"` (컷 하락=경쟁↑) / `"easier"` / `"stable"`

**기대 효과**: 연도별 입결 변화 트렌드를 한 눈에 파악 가능

---

## 🙋 MANUAL — 사람의 직접 확인/작업 필요

### M1. 고려대학교 정시 백분위 데이터 검증

**근거**: Q1 — `match_by_grade(grade=3.0, grade_type='수능등급')` 결과 고려대학교 정시 데이터 cut_70이 51~70 백분위 수준으로 표시됨 (예: 경제통계학부 67.93, 전자및정보공학과 68.47, 국제스포츠학부 51.93). 실제 고려대 정시는 국영수탐 각 90+ 백분위가 일반적이어서 데이터 신뢰도 의심됨.

**확인 필요 사항**:
1. `data/results_extracted/고려대학교/` JSON 파일 열어서 원본 값 확인
2. 고려대 입학처 공식 정시 입시결과와 대조 (수치가 60~70 범위인지 90+ 범위인지)
3. 만약 고려대가 자체 환산점수(가중합산 백분위) 방식을 사용한다면 score_type을 `'환산점수'`로 수정
4. 만약 데이터 자체가 오류라면 해당 레코드 삭제 또는 flag 처리

**확인 방법**:
```bash
python -c "
import sqlite3, json
conn = sqlite3.connect('data/admission.db')
rows = conn.execute('''
  SELECT d.name, r.process_name, r.score_type, r.cut_50, r.cut_70, r.result_year
  FROM admission_result r
  JOIN admission_department d ON d.id=r.department_id
  WHERE d.university=\"고려대학교\"
  LIMIT 20
''').fetchall()
for r in rows: print(r)
"
```

**예상 소요**: 30분 (공식 사이트 조회 포함)

---

### M2. 서강대학교 OCR 오류 학과명 수동 정정

**근거**: Q3 — 서강대 학과명에 "거머르트국저스무", "사릭과", "중교사과", "급도더인국릭부" 등 OCR 오류 존재. A4 휴리스틱으로 일부 필터 가능하나 근본 해결은 원본 PDF 대조 후 정정 필요.

**확인 필요 사항**:
1. `data/raw/서강대학교/` 원본 PDF 열기
2. 입시결과 PDF에서 해당 학과 페이지 찾아 실제 학과명 확인
3. 예상 매핑 (검증 필요):
   - "거머르트국저스무" → 게페르트국제학부(?)
   - "사릭과" → 사회학과(?)
   - "중교사과" → 중국문화학과(?)
   - "급도더인국릭부" → 글로벌한국학부(?)
4. 확인 후 Claude에게 매핑 표 전달 → `fix_sogang_depts.py` 스크립트로 DB 일괄 수정

**예상 소요**: 20분 (PDF 확인) + Claude 코딩 20분

---

### M3. 수능최저 미파싱 케이스 조사 (충북대·서강대 등)

**근거**: Q2/Q3 — 여러 전형에서 "수능최저 있음 (기준 미파싱)" 반환. student_suneung 제공해도 충족 여부 판별 불가.

**확인 필요 사항**:
1. 아래 쿼리로 미파싱 전형 목록 확인:
   ```bash
   python -c "
   import sqlite3, json
   conn = sqlite3.connect('data/admission.db')
   rows = conn.execute('''
     SELECT university, process_name, attributes FROM admission_process
     WHERE json_extract(attributes, '$.수능최저.있음') = 1
     AND json_extract(attributes, '$.수능최저.조건') IS NULL
     LIMIT 20
   ''').fetchall()
   for r in rows:
       attrs = json.loads(r[2] or '{}')
       print(r[0], r[1][:30], attrs.get('수능최저'))
   "
   ```
2. raw attributes에서 수능최저 원문 텍스트 확인
3. `src/parse_suneung_min.py`가 왜 파싱 못했는지 패턴 분석
4. 분석 결과를 Claude에게 전달 → 파서 규칙 추가

**예상 소요**: 30분 (패턴 확인) + Claude 코딩 30분

---

### M4. 2026학년도 입시결과 수집

**근거**: 현재 DB는 2024/2025학년도 입시결과만 보유. 2026학년도(2025 수능 기준) 결과가 가장 최신이며 현 수험생에게 가장 관련성 높음. 2026-03-17 현재 CDN에 미게시 상태.

**확인 및 작업 필요 사항**:
1. [adiga.kr](https://www.adiga.kr) 또는 CDN(`cdn013.negagea.net`)에서 2026학년도 결과 게시 여부 확인
2. `download_results.py` 에서 연도를 2026으로 변경 후 시험 다운로드:
   ```bash
   # download_results.py 상단 YEAR 변수 2026으로 수정 후:
   .venv/Scripts/python.exe download_results.py
   ```
3. 다운로드된 파일 확인 후 파이프라인 재실행:
   ```bash
   .venv/Scripts/python.exe extract_results_pdfs.py
   .venv/Scripts/python.exe extract_results_batch.py
   ```
4. 우선순위 대학 (CDN 미게시 시 직접 수집): 경희대, 중앙대, 한국외대, 서강대, 성균관대, 한양대, 이화여대, 광운대, 국민대

**예상 소요**: 자동화 가능하나 사람이 트리거 필요. 데이터 가용 시 Claude가 파이프라인 실행.

---

## 우선순위 정리

| 순위 | 태스크 | 유형 | 임팩트 | 난이도 |
|------|--------|------|--------|--------|
| 1 | **A3** compare_universities 반환 타입 수정 | AUTO | 버그 수정 | 낮음 |
| 2 | **A1** SYNONYMS 확장 (AI/인공지능 추가) | AUTO | Q4 개선 | 낮음 |
| 3 | **A2** 지역균형/인재 전형 제한 표시 | AUTO | Q6 개선 | 중간 |
| 4 | **A4** OCR 아티팩트 휴리스틱 강화 | AUTO | Q3 개선 | 중간 |
| 5 | **A5** get_process_detail 연도 그룹핑 | AUTO | Q4/Q5 UX | 중간 |
| 6 | **M4** 2026학년도 입시결과 수집 | **MANUAL** | 데이터 최신화 | 높음 |
| 7 | **M2** 서강대 OCR 학과명 정정 | **MANUAL** | Q3 품질 | 중간 |
| 8 | **M1** 고려대 정시 백분위 검증 | **MANUAL** | Q1 신뢰도 | 중간 |
| 9 | **M3** 수능최저 미파싱 케이스 조사 | **MANUAL** | Q2/Q3 충족판별 | 높음 |
