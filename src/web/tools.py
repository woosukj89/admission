"""Tool declarations and executor for the admission web client.

Defines 8 admission tools in Anthropic format and provides
execute_tool() to dispatch function calls to AdmissionStore.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.storage.admission_store import AdmissionStore

# ── Keyword expansion (동물, 컴퓨터 etc. → related terms) ─────────────────────

_KEYWORD_SYNONYMS: dict[str, list[str]] = {
    "동물":     ["동물", "동물자원", "동물생명", "축산", "수의", "반려동물", "동물보건", "야생동물"],
    "컴퓨터":   ["컴퓨터", "소프트웨어", "정보공학", "전산", "인공지능", "SW", "사이버"],
    "인공지능": ["인공지능", "AI", "데이터", "머신러닝", "빅데이터", "소프트웨어"],
    "전자":     ["전자", "전기전자", "전기", "반도체"],
    "간호":     ["간호", "간호학"],
    "약학":     ["약학", "약대", "제약"],
    "의학":     ["의학", "의예", "의과"],
}


def _expand_kw(keyword: str) -> list[str]:
    """Return expanded list of search terms for a given keyword."""
    kw = keyword.strip()
    return _KEYWORD_SYNONYMS.get(kw, [kw])


def _matches_process_type(process_name: str | None, process_type: str) -> bool:
    """Return True if process_name matches the requested process_type."""
    pname = (process_name or "").replace(" ", "")
    if process_type == "학생부교과":
        return "교과" in pname
    elif process_type == "학생부종합":
        return "종합" in pname or ("서류" in pname and "교과" not in pname)
    elif process_type == "논술위주":
        return "논술" in pname
    elif process_type == "수능위주":
        return "수능" in pname or "정시" in pname
    elif process_type == "실기/실적위주":
        return "실기" in pname or "실적" in pname
    return True


# ── Load metadata ──────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_META_PATH = _PROJECT_ROOT / "data" / "university_meta.json"
_UNI_META: dict[str, dict] = {}
if _META_PATH.exists():
    with open(_META_PATH, encoding="utf-8") as _f:
        _UNI_META = json.load(_f)

# ── Tool declarations ──────────────────────────────────────────────────────────

TOOL_LIST = [
    {
        "name": "search_programs",
        "description": "학과/전형 정보를 검색합니다. 키워드(컴퓨터공학, 간호학과 등), 지역, 계열로 필터링합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "major_keywords": {"type": "string", "description": "검색 키워드 (예: 컴퓨터공학, 간호학과)"},
                "region": {"type": "string", "description": "지역 필터 (예: 서울, 경기, 수도권, 지방)"},
                "track": {"type": "string", "description": "계열 (자연, 인문, 의약학, 예체능)"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본값 20)"},
            },
        },
    },
    {
        "name": "get_process_detail",
        "description": (
            "특정 대학교의 특정 전형에 대한 상세 정보를 가져옵니다. "
            "지원자격, 전형요소, 수능최저, 최근 입결 등을 포함합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "university": {"type": "string", "description": "대학교 이름 (예: 서강대학교)"},
                "process_name": {"type": "string", "description": "전형 이름 (예: 학생부종합(일반형))"},
                "department": {"type": "string", "description": "학과명 (선택, 없으면 전체 전형 정보 반환)"},
            },
            "required": ["university"],
        },
    },
    {
        "name": "match_by_grade",
        "description": (
            "학생 성적(내신 등급 또는 수능 등급/점수)으로 합격 가능한 학과 목록을 찾습니다. "
            "내신으로 수시, 수능등급으로 정시 검색. "
            "'서울 소재', '인서울', '서울에 있는 대학', '수도권 대학' 같은 지역 요청이 있으면 "
            "반드시 region 파라미터를 사용하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "grade": {"type": "number", "description": "성적 (등급: 1.0-9.0, 표준점수: 100-150, 백분위: 1-100)"},
                "grade_type": {"type": "string", "description": "성적 유형: '내신' (수시), '수능등급' (정시 등급), '표준점수', '백분위'"},
                "process_type": {
                    "type": "string",
                    "description": (
                        "전형 유형 필터. '학생부교과' (교과전형), '학생부종합' (종합전형), "
                        "'논술위주', '수능위주', '실기/실적위주'. "
                        "사용자가 '교과전형'을 요청하면 반드시 '학생부교과'를 사용하세요."
                    ),
                },
                "track": {"type": "string", "description": "계열 필터 (자연, 인문, 의약학 등)"},
                "region": {
                    "type": "string",
                    "description": (
                        "지역 필터. '서울', '수도권', '지방' 중 하나. "
                        "'인서울', '서울 소재', '서울에 있는', '서울 지역', '서울 대학교'(띄어쓰기) 같은 표현은 region='서울'. "
                        "'수도권 대학', '수도권 소재'는 region='수도권'."
                    ),
                },
                "university": {"type": "string", "description": "특정 대학교로 한정 (선택)"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본값 20)"},
            },
            "required": ["grade", "grade_type"],
        },
    },
    {
        "name": "list_universities",
        "description": "전국 대학교 목록을 지역/티어로 필터링하여 가져옵니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "지역 (서울, 경기, 수도권, 지방 등)"},
                "tier": {"type": "integer", "description": "대학 티어 (1=SKY, 2=상위권, 3=중위권, 4=하위권, 5=기타)"},
            },
        },
    },
    {
        "name": "search_fulltext",
        "description": "전형 내용/조건 전문 검색. 특정 키워드가 포함된 전형을 찾을 때 유용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 (예: 농어촌특별전형, 수능최저 없음)"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본값 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "compare_universities",
        "description": (
            "여러 대학교의 특정 학과 입결을 비교합니다. "
            "학생 성적과 각 대학 입결을 비교하여 합격 가능성을 평가합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "department_keyword": {"type": "string", "description": "학과 키워드 (예: 컴퓨터공학, 경영학)"},
                "universities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "비교할 대학교 목록",
                },
                "student_grade": {"type": "number", "description": "학생 성적 (내신 또는 수능 등급)"},
                "grade_type": {"type": "string", "description": "성적 유형: '내신' 또는 '수능등급'"},
            },
            "required": ["department_keyword"],
        },
    },
    {
        "name": "match_by_subjects",
        "description": (
            "과목별 내신/수능 등급을 입력하여 합격 가능한 학과를 찾습니다. "
            "계열에 따라 과목별 가중치를 적용해 유효 등급을 계산합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "korean": {"type": "number", "description": "국어 등급"},
                "math": {"type": "number", "description": "수학 등급"},
                "english": {"type": "number", "description": "영어 등급"},
                "social": {"type": "number", "description": "사회 등급 (인문계열)"},
                "science": {"type": "number", "description": "과학 등급 (자연계열)"},
                "grade_type": {"type": "string", "description": "'내신' 또는 '수능등급'"},
                "track": {"type": "string", "description": "계열 (자연, 인문). 가중치 계산에 사용됩니다."},
                "region": {"type": "string", "description": "지역 필터 (서울, 수도권 등)"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본값 20)"},
            },
            "required": ["grade_type"],
        },
    },
    {
        "name": "suggest_portfolio",
        "description": (
            "내신 등급을 기반으로 수시 지원 포트폴리오(6장)를 추천합니다. "
            "안정/추천/도전 버킷으로 나눠 대학별 1개씩 학과를 추천합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "grade": {"type": "number", "description": "내신 평균 등급 (1.0-9.0)"},
                "region": {"type": "string", "description": "선호 지역 (서울, 수도권, 지방 등)"},
                "track": {"type": "string", "description": "계열 (자연, 인문 등)"},
            },
            "required": ["grade"],
        },
    },
    {
        "name": "list_departments",
        "description": (
            "주어진 연도의 학과/전공명 목록을 반환합니다. "
            "학생이 '컴퓨터 학과', '패션 관련 학과'처럼 막연히 말하면 이 도구로 "
            "DB에 있는 정확한 학과명을 확인 후 match_by_grade의 major_keywords로 전달하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "입시 연도 (기본값 2025)"},
                "keyword": {"type": "string", "description": "학과명 필터 키워드 (예: 컴퓨터, 간호, 경영)"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본값 200)"},
            },
        },
    },
    {
        "name": "check_university_feasibility",
        "description": (
            "특정 대학교의 입학 가능성을 종합 분석합니다. "
            "해당 대학의 모든 전형을 조회하여 안정/추천/도전/불가 판정과 요약 통계를 반환합니다. "
            "'서울대 갈 수 있어?', '연세대 가능성은?' 같은 질문에 사용하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "university": {"type": "string", "description": "목표 대학교 이름 (예: 서울대학교)"},
                "grade": {"type": "number", "description": "학생 성적 (등급: 1.0-9.0)"},
                "grade_type": {"type": "string", "description": "'내신' (수시) 또는 '수능등급' (정시)"},
                "major_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "학과 키워드 필터 (선택, 예: ['경영', '경제'])",
                },
            },
            "required": ["university", "grade", "grade_type"],
        },
    },
]

# ── Tool executor ──────────────────────────────────────────────────────────────


def execute_tool(name: str, args: dict, store: AdmissionStore) -> str:
    """Dispatch a Gemini function call to the appropriate AdmissionStore method.
    Returns JSON string result."""
    try:
        if name == "search_programs":
            return _search_programs(args, store)
        elif name == "get_process_detail":
            return _get_process_detail(args, store)
        elif name == "match_by_grade":
            return _match_by_grade(args, store)
        elif name == "list_universities":
            return _list_universities(args)
        elif name == "search_fulltext":
            return _search_fulltext(args, store)
        elif name == "compare_universities":
            return _compare_universities(args, store)
        elif name == "match_by_subjects":
            return _match_by_subjects(args, store)
        elif name == "suggest_portfolio":
            return _suggest_portfolio(args, store)
        elif name == "list_departments":
            return _list_departments(args, store)
        elif name == "check_university_feasibility":
            return _check_university_feasibility(args, store)
        else:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── Individual tool implementations ───────────────────────────────────────────


def _search_programs(args: dict, store: AdmissionStore) -> str:
    keywords_raw = args.get("major_keywords", "")
    region_filter = args.get("region", "")
    track_filter = args.get("track", "")
    limit = min(int(args.get("limit", 20)), 50)

    # Expand keywords to related terms (e.g. "동물" → 동물자원, 축산, 수의 etc.)
    expanded = _expand_kw(keywords_raw) if keywords_raw else []

    seen_ids: set = set()
    all_depts: list = []
    if expanded:
        for kw in expanded:
            for d in store.find_departments(
                name=kw,
                track=track_filter if track_filter else None,
            ):
                if d["id"] not in seen_ids:
                    seen_ids.add(d["id"])
                    all_depts.append(d)
    else:
        all_depts = store.find_departments(
            track=track_filter if track_filter else None,
        )

    if region_filter:
        all_depts = [d for d in all_depts if _in_region(d.get("university", ""), region_filter)]

    depts = all_depts[:limit]
    results = []
    for d in depts:
        meta = _UNI_META.get(d.get("university", ""), {})
        results.append({
            "university": d.get("university"),
            "department": d.get("name"),
            "track": d.get("track"),
            "year": d.get("year"),
            "campus": d.get("campus"),
            "region": meta.get("region"),
        })
    return json.dumps({"programs": results, "total": len(results)}, ensure_ascii=False)


def _get_process_detail(args: dict, store: AdmissionStore) -> str:
    university = args.get("university", "")
    process_name = args.get("process_name", "")
    department = args.get("department", "")

    depts = store.find_departments(university=university)
    if not depts:
        return json.dumps({"error": f"{university} 정보를 찾을 수 없습니다."}, ensure_ascii=False)

    processes = []
    for dept in depts[:20]:
        procs = store.find_processes(
            department_id=dept["id"],
            process_name=process_name if process_name else None,
        )
        processes.extend(procs)

    if department:
        dept_map = {d["id"]: d for d in depts}
        processes = [
            p for p in processes
            if department in (dept_map.get(p.get("department_id"), {}).get("name", ""))
        ]

    result_rows = store.find_results(
        university=university,
        process_name=process_name if process_name else None,
        limit=10,
    )

    output = {
        "university": university,
        "processes_found": len(processes),
        "processes": [_fmt_process(p) for p in processes[:10]],
        "recent_results": [_fmt_result(r) for r in result_rows],
    }
    return json.dumps(output, ensure_ascii=False)


def _match_by_grade(args: dict, store: AdmissionStore) -> str:
    grade = float(args["grade"])
    grade_type = args["grade_type"]
    process_type = args.get("process_type", "")
    track_filter = args.get("track", "")
    region_filter = args.get("region", "")
    university_filter = args.get("university", "")
    limit = min(int(args.get("limit", 20)), 100)

    score_type_map = {
        "내신": "등급",
        "수능등급": "등급",
        "표준점수": "표준점수",
        "백분위": "백분위",
        "환산점수": "환산점수",
    }
    score_type = score_type_map.get(grade_type, "등급")

    results = store.find_results_by_score(
        student_grade=grade,
        score_type=score_type,
        grade_type=grade_type,
        limit=limit * 20,
    )

    if process_type:
        results = [r for r in results if _matches_process_type(r.get("process_name"), process_type)]
    if region_filter:
        results = [r for r in results if _in_region(r.get("university", ""), region_filter)]
    if track_filter:
        # Include departments with no track specified (accepts all tracks) OR matching track
        results = [r for r in results if not (r.get("track") or "").strip() or track_filter in (r.get("track") or "")]
    if university_filter:
        results = [r for r in results if university_filter in r.get("university", "")]

    results = results[:limit]

    items = []
    for r in results:
        meta = _UNI_META.get(r.get("university", ""), {})
        items.append({
            "university": r.get("university"),
            "department": r.get("department_name") or r.get("name"),
            "process_name": r.get("process_name"),
            "admission_type": r.get("admission_type"),
            "grade_type": r.get("grade_type"),
            "cut_70": r.get("cut_70"),
            "cut_50": r.get("cut_50"),
            "average_score": r.get("average_score"),
            "competition_rate": r.get("competition_rate"),
            "result_year": r.get("result_year"),
            "region": meta.get("region"),
        })
    return json.dumps({"results": items, "total": len(items)}, ensure_ascii=False)


def _list_universities(args: dict) -> str:
    region_filter = args.get("region", "")
    tier_filter = args.get("tier")

    unis = []
    for name, meta in _UNI_META.items():
        if region_filter and not _in_region(name, region_filter):
            continue
        if tier_filter and meta.get("tier") != int(tier_filter):
            continue
        unis.append({
            "university": name,
            "region": meta.get("region"),
            "region_broad": meta.get("region_broad"),
            "tier": meta.get("tier"),
        })

    unis.sort(key=lambda u: (u.get("tier", 9), u.get("university", "")))
    return json.dumps({"universities": unis, "total": len(unis)}, ensure_ascii=False)


def _search_fulltext(args: dict, store: AdmissionStore) -> str:
    query = args["query"]
    limit = min(int(args.get("limit", 10)), 30)

    results = store.search(query, table="process", limit=limit)
    items = []
    for r in results:
        items.append({
            "university": r.get("university"),
            "department": r.get("department_name") or r.get("name"),
            "process_name": r.get("process_name"),
            "snippet": (r.get("content") or "")[:200],
        })
    return json.dumps({"results": items, "total": len(items)}, ensure_ascii=False)


def _compare_universities(args: dict, store: AdmissionStore) -> str:
    dept_kw = args.get("department_keyword", "")
    universities = args.get("universities") or []
    student_grade = args.get("student_grade")
    grade_type = args.get("grade_type", "내신")

    score_type = "등급" if grade_type in ("내신", "수능등급") else grade_type

    comparison = []

    if universities:
        for uni in universities:
            results = store.find_results(
                university=uni,
                limit=300,
            )
            if dept_kw:
                results = [r for r in results if dept_kw in (r.get("department_name") or r.get("name") or "")]
            if not results:
                continue
            results_with_cut = [r for r in results if r.get("cut_70") is not None]
            if results_with_cut:
                best = min(results_with_cut, key=lambda r: abs(r.get("cut_70", 9) - (student_grade or 5)))
            else:
                best = results[0]

            entry: dict[str, Any] = {
                "university": uni,
                "department": best.get("department_name") or best.get("name"),
                "process_name": best.get("process_name"),
                "cut_70": best.get("cut_70"),
                "cut_50": best.get("cut_50"),
                "average_score": best.get("average_score"),
                "competition_rate": best.get("competition_rate"),
                "result_year": best.get("result_year"),
            }
            if student_grade is not None and best.get("cut_70") is not None:
                diff = best["cut_70"] - student_grade
                if score_type == "등급":
                    entry["verdict"] = "안정" if diff >= 0.3 else ("추천" if diff >= -0.2 else "도전")
                    entry["margin"] = round(diff, 2)
            comparison.append(entry)
    else:
        results = store.find_results(limit=50)
        if dept_kw:
            results = [r for r in results if dept_kw in (r.get("department_name") or r.get("name") or "")]
        seen_unis: set[str] = set()
        for r in results:
            uni = r.get("university", "")
            if uni in seen_unis:
                continue
            seen_unis.add(uni)
            comparison.append({
                "university": uni,
                "department": r.get("department_name") or r.get("name"),
                "process_name": r.get("process_name"),
                "cut_70": r.get("cut_70"),
                "competition_rate": r.get("competition_rate"),
                "result_year": r.get("result_year"),
            })

    return json.dumps({"comparison": comparison, "total": len(comparison)}, ensure_ascii=False)


def _match_by_subjects(args: dict, store: AdmissionStore) -> str:
    grade_type = args.get("grade_type", "내신")
    track = args.get("track", "인문")
    region = args.get("region", "")
    limit = min(int(args.get("limit", 20)), 100)

    grades = {k: v for k, v in {
        "korean": args.get("korean"),
        "math": args.get("math"),
        "english": args.get("english"),
        "social": args.get("social"),
        "science": args.get("science"),
    }.items() if v is not None}

    if not grades:
        return json.dumps({"error": "과목 등급을 입력해 주세요."}, ensure_ascii=False)

    if track == "자연":
        weights = {"math": 0.4, "science": 0.3, "korean": 0.2, "english": 0.1}
    else:
        weights = {"korean": 0.3, "english": 0.3, "math": 0.2, "social": 0.2}

    total_w = 0.0
    effective = 0.0
    for subj, w in weights.items():
        g = grades.get(subj)
        if g is not None:
            effective += g * w
            total_w += w

    if total_w > 0:
        effective = effective / total_w
    else:
        effective = sum(grades.values()) / len(grades)

    effective = round(effective, 2)

    result = json.loads(_match_by_grade(
        {"grade": effective, "grade_type": grade_type, "track": track, "region": region, "limit": limit},
        store,
    ))
    result["effective_grade"] = effective
    result["weights_used"] = track
    return json.dumps(result, ensure_ascii=False)


def _classify_process_type(process_name: str) -> str:
    """Classify a 전형 name into broad category for portfolio advice."""
    pn = process_name or ""
    if any(k in pn for k in ("교과", "일반고", "내신")):
        return "학생부교과"
    if any(k in pn for k in ("종합", "학종", "서류", "잠재", "역량", "인재")):
        return "학생부종합"
    if "논술" in pn:
        return "논술"
    if any(k in pn for k in ("실기", "예체능", "체육", "예술")):
        return "실기"
    if any(k in pn for k in ("농어촌", "기회균형", "사회배려", "특성화고", "기초생활")):
        return "특별전형"
    return "기타"


def _suggest_portfolio(args: dict, store: AdmissionStore) -> str:
    grade = float(args["grade"])
    region = args.get("region", "")
    track = args.get("track", "")

    results = store.find_results_by_score(
        student_grade=grade,
        score_type="등급",
        grade_type="내신",
        limit=500,
    )

    if region:
        results = [r for r in results if _in_region(r.get("university", ""), region)]
    if track:
        results = [r for r in results if track in (r.get("track") or "")]

    expanded_region = None
    if len(results) < 6 and region:
        all_results = store.find_results_by_score(
            student_grade=grade, score_type="등급", grade_type="내신", limit=500,
        )
        if track:
            all_results = [r for r in all_results if track in (r.get("track") or "")]
        results = all_results
        expanded_region = f"'{region}' 내 데이터 부족 → 전국으로 확장"

    안정, 추천, 도전 = [], [], []
    seen_unis: set[str] = set()

    for r in results:
        uni = r.get("university", "")
        if uni in seen_unis:
            continue
        cut = r.get("cut_70")
        if cut is None:
            continue
        diff = cut - grade
        process_name = r.get("process_name") or ""
        process_category = _classify_process_type(process_name)
        meta = _UNI_META.get(uni, {})
        entry = {
            "university": uni,
            "department": r.get("department_name") or r.get("name"),
            "process_name": process_name,
            "process_category": process_category,
            "cut_70": cut,
            "competition_rate": r.get("competition_rate"),
            "result_year": r.get("result_year"),
            "margin": round(diff, 2),
            "tier": meta.get("tier"),
            "region": meta.get("region"),
        }

        if diff >= 0.5:
            안정.append(entry)
        elif diff >= -0.3:
            추천.append(entry)
        elif diff >= -0.8:
            도전.append(entry)

        seen_unis.add(uni)

    portfolio = {
        "안정_label": "안정 (상향 대비 합격 확보)",
        "추천_label": "적정 (핵심 승부 라인)",
        "도전_label": "상향 (역전 도전)",
        "안정": 안정[:2],
        "추천": 추천[:2],
        "도전": 도전[:2],
        "student_grade": grade,
        "total_cards": len(안정[:2]) + len(추천[:2]) + len(도전[:2]),
        "portfolio_format": "수시 6장 전략: 상향 2장(도전) + 적정 2장(추천) + 안정 2장(안정)",
    }
    if expanded_region:
        portfolio["note_region_expanded"] = expanded_region

    return json.dumps(portfolio, ensure_ascii=False)


def _list_departments(args: dict, store: AdmissionStore) -> str:
    year = int(args.get("year", 2025))
    keyword = args.get("keyword") or None
    limit = min(int(args.get("limit", 200)), 500)
    names = store.list_distinct_departments(year=year, keyword=keyword, limit=limit)
    return json.dumps(
        {"year": year, "keyword": keyword, "total": len(names), "departments": names},
        ensure_ascii=False,
    )


def _check_university_feasibility(args: dict, store: AdmissionStore) -> str:
    import sqlite3 as _sqlite3
    import re as _re

    university = args["university"]
    grade = float(args["grade"])
    grade_type = args.get("grade_type", "내신")
    major_keywords = args.get("major_keywords") or []
    min_result_year = int(args.get("min_result_year", 2024))

    conn = _sqlite3.connect(str(store.db_path))
    conn.row_factory = _sqlite3.Row
    # Prefer exact university name match; fall back to substring
    exact_rows = conn.execute("""
        SELECT r.*, d.university, d.campus, d.track, d.name as department_name
        FROM admission_result r
        JOIN admission_department d ON d.id = r.department_id
        WHERE d.university = ? AND r.grade_type = ? AND r.result_year >= ?
        ORDER BY r.cut_70 ASC
        LIMIT 500
    """, (university, grade_type, min_result_year)).fetchall()
    if exact_rows:
        rows = exact_rows
    else:
        rows = conn.execute("""
            SELECT r.*, d.university, d.campus, d.track, d.name as department_name
            FROM admission_result r
            JOIN admission_department d ON d.id = r.department_id
            WHERE d.university LIKE ? AND r.grade_type = ? AND r.result_year >= ?
            ORDER BY r.cut_70 ASC
            LIMIT 500
        """, (f"%{university}%", grade_type, min_result_year)).fetchall()
    conn.close()

    results = [dict(r) for r in rows]
    if major_keywords:
        expanded: list[str] = []
        for kw in major_keywords:
            expanded.extend(_expand_kw(kw))
        results = [r for r in results
                   if any(kw in (r.get("department_name") or "") for kw in expanded)]

    summary = {"안정": 0, "추천": 0, "도전": 0, "불가": 0, "데이터없음": 0}
    items = []
    for r in results:
        cut_70 = r.get("cut_70")
        if cut_70 is None:
            verdict, margin = "데이터없음", None
        else:
            margin = cut_70 - grade
            verdict = ("안정" if margin >= 0.5 else
                       "추천" if margin >= 0 else
                       "도전" if margin >= -1.0 else "불가")
        summary[verdict] += 1
        meta = _UNI_META.get(r.get("university", ""), {})
        items.append({
            "department": _re.sub(r'(\d+\))+$|[*†‡··∙•]+$', '', r.get("department_name") or "").strip(),
            "process_name": r.get("process_name"),
            "admission_type": r.get("admission_type"),
            "cut_70": cut_70,
            "margin": round(margin, 2) if margin is not None else None,
            "verdict": verdict,
            "competition_rate": r.get("competition_rate"),
            "result_year": r.get("result_year"),
            "region": meta.get("region"),
        })

    _order = {"안정": 0, "추천": 1, "도전": 2, "불가": 3, "데이터없음": 4}
    items.sort(key=lambda x: (_order.get(x["verdict"], 9), x.get("cut_70") or 99))
    actual_univ = results[0].get("university", university) if results else university

    return json.dumps({
        "university": actual_univ,
        "student_grade": grade,
        "grade_type": grade_type,
        "summary": summary,
        "total": len(items),
        "results": items,
    }, ensure_ascii=False)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _in_region(university: str, region_filter: str) -> bool:
    meta = _UNI_META.get(university, {})
    uni_region = meta.get("region", "")
    uni_region_broad = meta.get("region_broad", "")
    rf = region_filter.strip()
    return rf in uni_region or rf in uni_region_broad or rf == uni_region or rf == uni_region_broad


def _fmt_process(p: dict) -> dict:
    attrs = p.get("attributes") or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except Exception:
            attrs = {}
    return {
        "process_name": p.get("process_name"),
        "process_type": p.get("process_type"),
        "quota": p.get("quota"),
        "전형요소": attrs.get("전형요소"),
        "수능최저": attrs.get("수능최저"),
    }


def _fmt_result(r: dict) -> dict:
    return {
        "university": r.get("university"),
        "department": r.get("department_name") or r.get("name"),
        "process_name": r.get("process_name"),
        "result_year": r.get("result_year"),
        "admission_type": r.get("admission_type"),
        "grade_type": r.get("grade_type"),
        "cut_70": r.get("cut_70"),
        "cut_50": r.get("cut_50"),
        "average_score": r.get("average_score"),
        "competition_rate": r.get("competition_rate"),
    }
