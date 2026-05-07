For each file under data/results, extract them to json for easier reading. It includes PDFs, XLXS, HWP, PNG, and other formats.
You can reference admission-extractor subagent for example on process.
Your goal is to achieve below.
Given,
**Department record:**
```python
dept_id = store.upsert_department(
    year=2026,
    university="가천대학교",
    campus=None,          # or "글로벌", "메디컬" etc. if specified
    track="인문",          # 계열 if identifiable: 인문, 자연, 예체능, 공학, 의약학, etc.
    name="경영학부",       # 학과/모집단위 name exactly as in the document
)
```

For each matching department and year, create a results record that looks something like:

**Results record:**
type: 정시 / 수시 / or others
average_score: decimal

Each university also provides 60, 70, 80 CUT. Make sure to save this as well, while also creating a common measure for all universities based on those values to query and search later.

Later, it will be used to query prompts like:
"내 5과목 성적 평균은 3.45야. 수학, 영어 만 하면 4.2야. 난 경영학부에 가고 싶어. 지역은 서울 아니면 경기도면 좋겠어. 갈 만한 대학교를 찾아줘."