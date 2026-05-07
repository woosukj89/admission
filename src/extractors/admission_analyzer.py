"""Analyze admission data from extracted PDF tables.

Parse and display admission statistics including:
- 경쟁률 (Competition ratio)
- 실기점수 (Practical exam score)
- 교과평균 (Subject average)
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Fix encoding for Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def load_json(file_path: str) -> dict:
    """Load JSON file with UTF-8 encoding."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_js_bn_general(table: dict) -> list[dict]:
    """Parse 정시 일반전형 table (general admission).

    Table structure (38 columns):
    - 0-4: 계열, 단과대학, 학과, 전공, 소재지
    - 5-7: 2026 모집인원 (가군, 나군, 다군)
    - 8-12: 2025 가군 (모집인원, 수시이월, 경쟁률, 추가합격률, 추가합격인원)
    - 13-17: 2025 나군
    - 18-22: 2025 다군
    - 23-27: 2024 가군
    - 28-32: 2024 나군
    - 33-37: 2024 다군
    """
    records = []
    data = table['data']

    for row in data:
        if len(row) < 20:
            continue

        # Skip header-like rows (check if index 10 is header text)
        if row[10] == '경 쟁 률' or row[0] == '계열':
            continue

        # Try to parse - skip if values don't make sense
        경쟁률_가군 = _to_float(row[10])
        if 경쟁률_가군 is None:
            continue

        record = {
            '계열': row[0],
            '단과대학': row[1],
            '학과': row[2],
            '전공': row[3] if row[3] != row[2] else None,  # Skip if same as 학과
            '소재지': row[4],
            '2026_모집인원_가군': _to_int(row[5]),
            '2026_모집인원_나군': _to_int(row[6]),
            '2026_모집인원_다군': _to_int(row[7]),
            '2025_가군_모집인원': _to_int(row[8]),
            '2025_가군_경쟁률': 경쟁률_가군,
            '2025_가군_추가합격률': _to_float(row[11]),
            '2025_나군_모집인원': _to_int(row[13]),
            '2025_나군_경쟁률': _to_float(row[15]),
            '2025_나군_추가합격률': _to_float(row[16]),
            '2024_가군_모집인원': _to_int(row[23]) if len(row) > 23 else None,
            '2024_가군_경쟁률': _to_float(row[25]) if len(row) > 25 else None,
            '2024_나군_모집인원': _to_int(row[28]) if len(row) > 28 else None,
            '2024_나군_경쟁률': _to_float(row[30]) if len(row) > 30 else None,
        }

        records.append(record)

    return records


def parse_js_bn_practical(table: dict) -> list[dict]:
    """Parse 정시 실기전형 table (practical exam admission).

    Returns list of records with:
    - 계열, 단과대학, 학부, 세부전공, 캠퍼스
    - 2026 모집인원
    - 2025 경쟁률, 충원율, 실기점수
    - 2024 경쟁률, 충원율, 실기점수
    """
    records = []
    data = table['data']

    for row in data:
        if len(row) < 20:
            continue

        # Skip header-like rows
        if row[0] == '계열' or row[9] == '경쟁률':
            continue

        record = {
            '계열': row[0],
            '단과대학': row[1],
            '학부': row[2],
            '세부전공': row[3],
            '캠퍼스': row[5],
            '2026_모집인원_가군': _to_int(row[6]),
            '2026_모집인원_나군': _to_int(row[7]),
            '2025_가군_모집인원': _to_int(row[8]),
            '2025_가군_경쟁률': _to_float(row[9]),
            '2025_가군_충원율': _to_float(row[10]),
            '2025_가군_실기점수': _to_float(row[11]),
            '2025_나군_모집인원': _to_int(row[12]),
            '2025_나군_경쟁률': _to_float(row[13]),
            '2025_나군_충원율': _to_float(row[14]),
            '2025_나군_실기점수': _to_float(row[15]),
            '2024_가군_모집인원': _to_int(row[16]),
            '2024_가군_경쟁률': _to_float(row[17]),
            '2024_가군_충원율': _to_float(row[18]),
            '2024_가군_실기점수': _to_float(row[19]),
            '2024_나군_모집인원': _to_int(row[20]) if len(row) > 20 else None,
            '2024_나군_경쟁률': _to_float(row[21]) if len(row) > 21 else None,
            '2024_나군_충원율': _to_float(row[22]) if len(row) > 22 else None,
            '2024_나군_실기점수': _to_float(row[23]) if len(row) > 23 else None,
        }

        # Only add if has valid data
        if record['세부전공'] and (record['2025_가군_실기점수'] or record['2025_나군_실기점수']):
            records.append(record)

    return records


def parse_ss_bn_early(table: dict) -> list[dict]:
    """Parse 수시 예체능 table (early admission for arts).

    Returns list of records with:
    - 캠퍼스, 단과대학, 모집단위
    - 경쟁률, 충원율, 교과평균, 실기평균
    """
    records = []
    data = table['data']

    for row in data:
        if len(row) < 7:
            continue

        # Skip header-like rows
        if row[0] == '캠퍼스':
            continue

        record = {
            '캠퍼스': row[0],
            '단과대학': row[1],
            '모집단위': row[2],
            '경쟁률': _to_float(row[3]),
            '충원율': _to_float(row[4]),
            '교과평균': _to_float(row[5]),
            '실기평균': _to_float(row[6]),
        }

        # Only add if has valid data
        if record['모집단위'] and (record['교과평균'] or record['실기평균']):
            records.append(record)

    return records


def _to_float(value) -> Optional[float]:
    """Convert value to float, return None if invalid."""
    if value is None:
        return None
    try:
        # Remove commas and convert
        return float(str(value).replace(',', ''))
    except (ValueError, TypeError):
        return None


def _to_int(value) -> Optional[int]:
    """Convert value to int, return None if invalid."""
    if value is None:
        return None
    try:
        return int(str(value).replace(',', ''))
    except (ValueError, TypeError):
        return None


def analyze_js_bn(file_path: str):
    """Analyze 2026_js_bn.json and display admission statistics."""
    data = load_json(file_path)

    print('=' * 100)
    print('중앙대학교 2026학년도 정시모집 입시결과 분석')
    print('=' * 100)

    # Find page 4 with the tables
    for page in data['pages']:
        if page['page_number'] == 4 and len(page['tables']) >= 2:
            # Table 1: General admission
            general_table = page['tables'][0]
            general_records = parse_js_bn_general(general_table)

            print('\n\n[ 정시 일반전형 - 모집단위별 경쟁률 (2025학년도) ]')
            print('-' * 100)
            print(f'{"계열":^6} {"단과대학":^14} {"학과":^22} {"전공":^14} {"가군경쟁률":^10} {"나군경쟁률":^10}')
            print('-' * 100)

            for r in general_records[:40]:
                print(f'{r["계열"]:^6} {r["단과대학"]:^14} {r["학과"]:^22} {r["전공"] or "-":^14} '
                      f'{r["2025_가군_경쟁률"] or "-":^10} {r["2025_나군_경쟁률"] or "-":^10}')

            # Table 2: Practical exam admission
            practical_table = page['tables'][1]
            practical_records = parse_js_bn_practical(practical_table)

            print('\n\n[ 정시 실기전형 - 모집단위별 실기점수 (2025학년도) ]')
            print('-' * 100)
            print(f'{"학부":^20} {"세부전공":^20} {"캠퍼스":^8} {"가군경쟁률":^10} {"가군실기점수":^12} {"나군경쟁률":^10} {"나군실기점수":^12}')
            print('-' * 100)

            for r in practical_records:
                가군점수 = f'{r["2025_가군_실기점수"]:.1f}' if r['2025_가군_실기점수'] else '-'
                나군점수 = f'{r["2025_나군_실기점수"]:.1f}' if r['2025_나군_실기점수'] else '-'
                가군경쟁 = f'{r["2025_가군_경쟁률"]:.1f}' if r['2025_가군_경쟁률'] else '-'
                나군경쟁 = f'{r["2025_나군_경쟁률"]:.1f}' if r['2025_나군_경쟁률'] else '-'

                print(f'{r["학부"]:^20} {r["세부전공"]:^20} {r["캠퍼스"]:^8} '
                      f'{가군경쟁:^10} {가군점수:^12} {나군경쟁:^10} {나군점수:^12}')


def analyze_ss_bn(file_path: str):
    """Analyze 2026_ss_bn.json and display admission statistics."""
    data = load_json(file_path)

    print('=' * 100)
    print('중앙대학교 2025학년도 수시모집 입시결과 분석')
    print('=' * 100)

    # Page 1 has the arts admission table
    for page in data['pages']:
        if page['page_number'] == 1 and page['tables']:
            table = page['tables'][0]
            records = parse_ss_bn_early(table)

            print('\n\n[ 수시 실기/실적 전형 - 모집단위별 평균 성적 ]')
            print('-' * 90)
            print(f'{"캠퍼스":^8} {"단과대학":^12} {"모집단위":^35} {"경쟁률":^8} {"교과평균":^10} {"실기평균":^10}')
            print('-' * 90)

            for r in records:
                교과 = f'{r["교과평균"]:.1f}' if r['교과평균'] else '-'
                실기 = f'{r["실기평균"]:.1f}' if r['실기평균'] else '-'
                경쟁 = f'{r["경쟁률"]:.1f}' if r['경쟁률'] else '-'

                print(f'{r["캠퍼스"]:^8} {r["단과대학"]:^12} {r["모집단위"]:^35} '
                      f'{경쟁:^8} {교과:^10} {실기:^10}')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Analyze admission data')
    parser.add_argument('file', help='Path to extracted JSON file')
    args = parser.parse_args()

    file_path = Path(args.file)

    if 'js_bn' in file_path.name:
        analyze_js_bn(str(file_path))
    elif 'ss_bn' in file_path.name:
        analyze_ss_bn(str(file_path))
    else:
        print(f'Unknown file type: {file_path.name}')
