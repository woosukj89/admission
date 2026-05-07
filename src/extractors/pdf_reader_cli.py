"""Command-line interface for PDF table extraction.

Usage:
    python -m src.extractors.pdf_reader_cli <pdf_path> [options]

Examples:
    python -m src.extractors.pdf_reader_cli reference/2026_ss_bn.pdf
    python -m src.extractors.pdf_reader_cli reference/2026_ss_bn.pdf --output data/extracted/
    python -m src.extractors.pdf_reader_cli reference/*.pdf --output data/extracted/
"""

import argparse
import json
import sys
from pathlib import Path

# Fix encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.table_extractor import PDFDocumentReader, PDFTableExtractor


def main():
    parser = argparse.ArgumentParser(
        description="Extract tables and text from PDF files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Extract single PDF to JSON:
    python -m src.extractors.pdf_reader_cli reference/2026_ss_bn.pdf

  Extract with custom output directory:
    python -m src.extractors.pdf_reader_cli reference/*.pdf -o data/extracted/

  Extract only tables (no full text):
    python -m src.extractors.pdf_reader_cli reference/2026_ss_bn.pdf --tables-only

  Extract specific pages:
    python -m src.extractors.pdf_reader_cli reference/document.pdf --pages 1-10
        """,
    )

    parser.add_argument(
        "pdf_files",
        nargs="+",
        help="PDF file(s) to process",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output directory for JSON files (default: same as input)",
    )
    parser.add_argument(
        "--tables-only",
        action="store_true",
        help="Extract only tables, not full page text",
    )
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Page range to extract (e.g., '1-10' or '1,3,5')",
    )
    parser.add_argument(
        "--no-forward-fill",
        action="store_true",
        help="Disable forward-filling of merged cells",
    )
    parser.add_argument(
        "--format",
        choices=["json", "jsonl", "csv"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Parse page range if specified
    pages = None
    if args.pages:
        pages = []
        for part in args.pages.split(","):
            if "-" in part:
                start, end = part.split("-")
                pages.extend(range(int(start), int(end) + 1))
            else:
                pages.append(int(part))

    # Setup output directory
    output_dir = Path(args.output) if args.output else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Process each PDF file
    for pdf_path_str in args.pdf_files:
        pdf_path = Path(pdf_path_str)

        if not pdf_path.exists():
            print(f"Error: File not found: {pdf_path}", file=sys.stderr)
            continue

        if not pdf_path.suffix.lower() == ".pdf":
            print(f"Skipping non-PDF file: {pdf_path}", file=sys.stderr)
            continue

        if args.verbose:
            print(f"Processing: {pdf_path}")

        try:
            if args.tables_only:
                # Extract only tables
                extractor = PDFTableExtractor(forward_fill=not args.no_forward_fill)
                data = extractor.extract_to_json(pdf_path, pages)
            else:
                # Full extraction
                reader = PDFDocumentReader()
                reader.table_extractor = PDFTableExtractor(forward_fill=not args.no_forward_fill)
                data = reader.extract_full(pdf_path)

                # Filter pages if specified
                if pages:
                    data["pages"] = [p for p in data["pages"] if p["page_number"] in pages]

            # Determine output path
            if output_dir:
                output_path = output_dir / f"{pdf_path.stem}.json"
            else:
                output_path = pdf_path.with_suffix(".json")

            # Write output
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            if args.verbose:
                table_count = len(data.get("tables", [])) if args.tables_only else sum(
                    p.get("table_count", 0) for p in data.get("pages", [])
                )
                print(f"  Saved to: {output_path}")
                print(f"  Tables found: {table_count}")

        except Exception as e:
            print(f"Error processing {pdf_path}: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    main()
