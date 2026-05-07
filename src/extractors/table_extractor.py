"""Enhanced PDF table extractor for Korean university admission documents.

Handles complex tables with:
- Merged cells (forward-filling)
- Multi-level headers
- Korean text normalization
- Structured JSON output
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ..utils.korean import normalize_korean
from ..utils.logging import get_logger

logger = get_logger("extractors.table")


@dataclass
class TableCell:
    """Represents a single cell in a table."""
    value: Optional[str]
    row: int
    col: int
    is_header: bool = False
    is_merged: bool = False

    def __post_init__(self):
        if self.value:
            # Normalize and clean the value
            self.value = normalize_korean(self.value.strip())
            # Collapse multiple whitespace/newlines
            self.value = re.sub(r'\s+', ' ', self.value)


@dataclass
class ExtractedTable:
    """Represents an extracted table with metadata."""
    page_number: int
    table_index: int
    rows: int
    cols: int
    headers: list[list[str]]
    data: list[list[Optional[str]]]
    raw_data: list[list[Optional[str]]] = field(default_factory=list)
    bbox: Optional[tuple[float, float, float, float]] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "page": self.page_number,
            "table_index": self.table_index,
            "dimensions": {"rows": self.rows, "cols": self.cols},
            "headers": self.headers,
            "data": self.data,
            "bbox": self.bbox,
        }

    def to_records(self, flatten_headers: bool = True) -> list[dict]:
        """Convert table to list of record dictionaries.

        Args:
            flatten_headers: If True, combine multi-level headers into single keys

        Returns:
            List of dictionaries, one per data row
        """
        if not self.headers or not self.data:
            return []

        if flatten_headers and len(self.headers) > 1:
            column_names = self._flatten_headers()
        else:
            column_names = self.headers[-1] if self.headers else []

        records = []
        for row in self.data:
            record = {}
            for i, value in enumerate(row):
                if i < len(column_names) and column_names[i]:
                    key = column_names[i]
                else:
                    key = f"col_{i}"
                record[key] = value
            records.append(record)

        return records

    def _flatten_headers(self) -> list[str]:
        """Flatten multi-level headers into single column names.

        For headers like:
            ['전형', None, None]
            ['모집인원', '지원인원', '경쟁률']

        Returns: ['전형_모집인원', '전형_지원인원', '전형_경쟁률']
        """
        if not self.headers:
            return []

        num_cols = max(len(row) for row in self.headers)
        flattened = []

        for col_idx in range(num_cols):
            parts = []
            last_value = None

            for row in self.headers:
                if col_idx < len(row):
                    value = row[col_idx]
                    if value and value != last_value:
                        parts.append(value)
                        last_value = value

            if parts:
                flattened.append("_".join(parts))
            else:
                flattened.append(f"col_{col_idx}")

        return flattened

    def to_flat_dict(self) -> dict:
        """Convert to a flat dictionary with flattened headers."""
        return {
            "page": self.page_number,
            "table_index": self.table_index,
            "dimensions": {"rows": self.rows, "cols": self.cols},
            "column_names": self._flatten_headers() if len(self.headers) > 1 else (self.headers[0] if self.headers else []),
            "data": self.data,
        }


class PDFTableExtractor:
    """Extract tables from PDF files with proper handling of merged cells."""

    def __init__(self, forward_fill: bool = True, detect_headers: bool = True):
        """Initialize the table extractor.

        Args:
            forward_fill: Whether to fill merged cells with previous values
            detect_headers: Whether to auto-detect header rows
        """
        self.forward_fill = forward_fill
        self.detect_headers = detect_headers
        self._fitz = None

    def _get_fitz(self):
        """Lazy import PyMuPDF."""
        if self._fitz is None:
            try:
                import fitz
                self._fitz = fitz
            except ImportError:
                raise ImportError(
                    "PyMuPDF is required for PDF table extraction. "
                    "Install it with: pip install PyMuPDF"
                )
        return self._fitz

    def _forward_fill_row(self, row: list[Optional[str]], previous_row: Optional[list[Optional[str]]] = None) -> list[Optional[str]]:
        """Forward-fill None values in a row.

        For vertical merges, use previous row values.
        For horizontal merges, use previous column value in same row.
        """
        filled = list(row)

        for i, value in enumerate(filled):
            if value is None:
                # First try previous row (vertical merge)
                if previous_row and i < len(previous_row) and previous_row[i]:
                    filled[i] = previous_row[i]

        return filled

    def _detect_header_rows(self, data: list[list[Optional[str]]]) -> int:
        """Detect how many rows are headers.

        Headers typically:
        - Are at the top
        - Have more None values (merged cells spanning columns)
        - Contain certain keywords
        - Don't contain numeric data patterns
        """
        if not data:
            return 0

        header_keywords = {'구분', '전형', '모집', '단위', '계열', '학과', '학부',
                          '캠퍼스', '대학', '인원', '비율', '유형', '자격', '평균',
                          '경쟁률', '충원', '지원', '합격', '등록', 'CUT'}

        def get_numeric_ratio(row):
            """Get ratio of numeric values in row."""
            numeric_count = 0
            total_count = 0
            for v in row:
                if v is None:
                    continue
                total_count += 1
                # Check if value is numeric or percentage-like
                if re.match(r'^[\d.,\-]+%?$', str(v).strip()):
                    numeric_count += 1
            return numeric_count / total_count if total_count > 0 else 0

        def is_data_row(row):
            """Check if row is a data row (contains significant numeric content)."""
            numeric_ratio = get_numeric_ratio(row)
            # If more than 30% of non-None values are numeric, it's likely data
            return numeric_ratio > 0.3

        header_rows = 1  # At least first row is header

        for i, row in enumerate(data[1:5], start=1):  # Check rows 1-4 only
            # If row is clearly data, stop
            if is_data_row(row):
                break

            row_text = ' '.join(str(v) for v in row if v)

            # Check for header keywords
            has_keywords = any(kw in row_text for kw in header_keywords)

            # Check for high None ratio (merged header cells - common in headers)
            none_ratio = sum(1 for v in row if v is None) / len(row) if row else 0

            # A row is a header if it has keywords OR has many merged cells (None values)
            # but not if it has too many numeric values
            if has_keywords and get_numeric_ratio(row) < 0.2:
                header_rows = i + 1
            elif none_ratio > 0.5 and get_numeric_ratio(row) < 0.2:
                header_rows = i + 1
            else:
                break

        return header_rows

    def _clean_cell_value(self, value: Optional[str]) -> Optional[str]:
        """Clean and normalize a cell value."""
        if value is None:
            return None

        # Normalize Korean text
        value = normalize_korean(value.strip())

        # Collapse whitespace
        value = re.sub(r'\s+', ' ', value)

        # Remove empty values
        if not value or value in ('-', ''):
            return None

        return value

    def _process_table(self, table, page_num: int, table_idx: int) -> ExtractedTable:
        """Process a single table from PyMuPDF."""
        raw_data = table.extract()

        if not raw_data:
            return ExtractedTable(
                page_number=page_num,
                table_index=table_idx,
                rows=0,
                cols=0,
                headers=[],
                data=[],
                raw_data=[],
            )

        # Clean all cell values
        cleaned_data = []
        for row in raw_data:
            cleaned_row = [self._clean_cell_value(cell) for cell in row]
            cleaned_data.append(cleaned_row)

        # Forward-fill if enabled
        if self.forward_fill:
            filled_data = []
            previous_row = None
            for row in cleaned_data:
                filled_row = self._forward_fill_row(row, previous_row)
                filled_data.append(filled_row)
                previous_row = filled_row
            cleaned_data = filled_data

        # Detect headers if enabled
        if self.detect_headers:
            num_headers = self._detect_header_rows(cleaned_data)
        else:
            num_headers = 1

        headers = cleaned_data[:num_headers]
        data = cleaned_data[num_headers:]

        # Get bounding box if available
        bbox = None
        if hasattr(table, 'bbox'):
            bbox = tuple(table.bbox)

        return ExtractedTable(
            page_number=page_num,
            table_index=table_idx,
            rows=table.row_count,
            cols=table.col_count,
            headers=headers,
            data=data,
            raw_data=raw_data,
            bbox=bbox,
        )

    def extract_tables(self, pdf_path: Union[str, Path], pages: Optional[list[int]] = None) -> list[ExtractedTable]:
        """Extract all tables from a PDF file.

        Args:
            pdf_path: Path to the PDF file
            pages: Optional list of page numbers (1-indexed) to extract from.
                   If None, extracts from all pages.

        Returns:
            List of ExtractedTable objects
        """
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            logger.error(f"PDF file not found: {pdf_path}")
            return []

        tables = []

        try:
            doc = fitz.open(str(pdf_path))

            page_range = range(len(doc))
            if pages:
                # Convert 1-indexed to 0-indexed
                page_range = [p - 1 for p in pages if 0 < p <= len(doc)]

            for page_idx in page_range:
                page = doc[page_idx]
                page_tables = page.find_tables()

                for table_idx, table in enumerate(page_tables.tables):
                    extracted = self._process_table(table, page_idx + 1, table_idx + 1)
                    if extracted.rows > 0:
                        tables.append(extracted)

            doc.close()

        except Exception as e:
            logger.error(f"Error extracting tables from {pdf_path}: {e}")

        return tables

    def extract_to_json(self, pdf_path: Union[str, Path], pages: Optional[list[int]] = None) -> dict:
        """Extract tables and return as JSON-serializable dict.

        Args:
            pdf_path: Path to the PDF file
            pages: Optional list of page numbers to extract from

        Returns:
            Dictionary with file info and extracted tables
        """
        pdf_path = Path(pdf_path)
        tables = self.extract_tables(pdf_path, pages)

        return {
            "source_file": str(pdf_path.name),
            "total_tables": len(tables),
            "tables": [t.to_dict() for t in tables],
        }

    def extract_to_records(self, pdf_path: Union[str, Path], pages: Optional[list[int]] = None) -> list[dict]:
        """Extract tables and return as flat list of record dicts.

        Useful for loading into pandas or similar.

        Args:
            pdf_path: Path to the PDF file
            pages: Optional list of page numbers to extract from

        Returns:
            List of record dictionaries with table metadata
        """
        tables = self.extract_tables(pdf_path, pages)

        all_records = []
        for table in tables:
            records = table.to_records()
            for record in records:
                record['_page'] = table.page_number
                record['_table'] = table.table_index
                all_records.append(record)

        return all_records


class PDFDocumentReader:
    """High-level PDF reader for admission documents.

    Extracts both text and structured table data.
    """

    def __init__(self):
        self.table_extractor = PDFTableExtractor()
        self._fitz = None

    def _get_fitz(self):
        """Lazy import PyMuPDF."""
        if self._fitz is None:
            try:
                import fitz
                self._fitz = fitz
            except ImportError:
                raise ImportError(
                    "PyMuPDF is required. Install it with: pip install PyMuPDF"
                )
        return self._fitz

    def extract_text_by_page(self, pdf_path: Union[str, Path]) -> list[str]:
        """Extract text from each page."""
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return []

        pages = []
        try:
            doc = fitz.open(str(pdf_path))
            for page in doc:
                text = page.get_text()
                if text:
                    text = normalize_korean(text)
                pages.append(text)
            doc.close()
        except Exception as e:
            logger.error(f"Error reading PDF {pdf_path}: {e}")

        return pages

    def extract_full(self, pdf_path: Union[str, Path]) -> dict:
        """Extract complete document with text and tables.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Dictionary with:
            - metadata: file info
            - pages: list of page data with text and tables
        """
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            logger.error(f"PDF file not found: {pdf_path}")
            return {"error": "File not found"}

        result = {
            "source_file": str(pdf_path.name),
            "file_path": str(pdf_path),
            "pages": [],
            "metadata": {},
        }

        try:
            doc = fitz.open(str(pdf_path))

            # Get metadata
            result["metadata"] = {
                "page_count": len(doc),
                "title": doc.metadata.get("title", ""),
                "author": doc.metadata.get("author", ""),
                "subject": doc.metadata.get("subject", ""),
                "creator": doc.metadata.get("creator", ""),
            }

            # Extract page by page
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                page_num = page_idx + 1

                # Get text
                text = page.get_text()
                if text:
                    text = normalize_korean(text)

                # Get tables
                page_tables = page.find_tables()
                tables = []
                for table_idx, table in enumerate(page_tables.tables):
                    extracted = self.table_extractor._process_table(table, page_num, table_idx + 1)
                    if extracted.rows > 0:
                        tables.append(extracted.to_dict())

                result["pages"].append({
                    "page_number": page_num,
                    "text": text,
                    "tables": tables,
                    "table_count": len(tables),
                })

            doc.close()

        except Exception as e:
            logger.error(f"Error extracting from {pdf_path}: {e}")
            result["error"] = str(e)

        return result

    def save_as_json(self, pdf_path: Union[str, Path], output_path: Optional[Union[str, Path]] = None) -> Path:
        """Extract PDF and save as JSON file.

        Args:
            pdf_path: Path to the PDF file
            output_path: Optional output path. If None, uses same name as PDF with .json extension

        Returns:
            Path to the saved JSON file
        """
        pdf_path = Path(pdf_path)

        if output_path is None:
            output_path = pdf_path.with_suffix('.json')
        else:
            output_path = Path(output_path)

        data = self.extract_full(pdf_path)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved extracted data to {output_path}")
        return output_path


def extract_pdf_tables(pdf_path: Union[str, Path], pages: Optional[list[int]] = None) -> list[ExtractedTable]:
    """Convenience function to extract tables from a PDF.

    Args:
        pdf_path: Path to the PDF file
        pages: Optional list of page numbers (1-indexed)

    Returns:
        List of ExtractedTable objects
    """
    extractor = PDFTableExtractor()
    return extractor.extract_tables(pdf_path, pages)


def extract_pdf_to_json(pdf_path: Union[str, Path], output_path: Optional[Union[str, Path]] = None) -> dict:
    """Convenience function to extract PDF and optionally save as JSON.

    Args:
        pdf_path: Path to the PDF file
        output_path: Optional path to save JSON output

    Returns:
        Extracted data as dictionary
    """
    reader = PDFDocumentReader()
    data = reader.extract_full(pdf_path)

    if output_path:
        output_path = Path(output_path)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return data
