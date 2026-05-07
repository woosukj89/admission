"""PDF content extractor using PyMuPDF"""

import os
import sys
from pathlib import Path
from typing import Optional, Union

from ..utils.korean import normalize_korean
from ..utils.logging import get_logger

logger = get_logger("extractors.pdf")


def _suppress_mupdf_warnings():
    """Suppress MuPDF stderr warnings (CID font errors, etc.)"""
    # MuPDF outputs warnings to stderr for CID font issues common in Korean PDFs
    # These are non-fatal and clutter the output
    pass


class PDFExtractor:
    """Extract text and metadata from PDF files"""

    def __init__(self):
        self._fitz = None
        self._warnings_suppressed = False

    def _get_fitz(self):
        """Lazy import PyMuPDF and suppress warnings"""
        if self._fitz is None:
            try:
                import fitz

                # Suppress MuPDF warnings about CID fonts
                # These are common in Korean PDFs and are non-fatal
                if not self._warnings_suppressed:
                    try:
                        # PyMuPDF 1.24+ has TOOLS.mupdf_warnings()
                        if hasattr(fitz, 'TOOLS'):
                            fitz.TOOLS.mupdf_warnings(False)
                    except Exception:
                        pass

                    # Also try to set message handler to suppress
                    try:
                        if hasattr(fitz, 'TOOLS') and hasattr(fitz.TOOLS, 'set_messages'):
                            fitz.TOOLS.set_messages(False)
                    except Exception:
                        pass

                    self._warnings_suppressed = True

                self._fitz = fitz
            except ImportError:
                raise ImportError(
                    "PyMuPDF is required for PDF extraction. "
                    "Install it with: pip install PyMuPDF"
                )
        return self._fitz

    def _suppress_stderr(self):
        """Context manager to suppress stderr (MuPDF warnings) at file descriptor level"""
        import contextlib

        @contextlib.contextmanager
        def suppress():
            # MuPDF writes directly to stderr file descriptor, not Python's sys.stderr
            # We need to redirect at the OS level
            stderr_fd = sys.stderr.fileno()
            old_stderr_fd = os.dup(stderr_fd)
            devnull_fd = os.open(os.devnull, os.O_WRONLY)

            try:
                os.dup2(devnull_fd, stderr_fd)
                yield
            finally:
                os.dup2(old_stderr_fd, stderr_fd)
                os.close(old_stderr_fd)
                os.close(devnull_fd)

        return suppress()

    def extract_text(self, pdf_path: Union[str, Path]) -> str:
        """Extract all text from a PDF file"""
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            logger.error(f"PDF file not found: {pdf_path}")
            return ""

        try:
            with self._suppress_stderr():
                doc = fitz.open(str(pdf_path))
                text_parts = []

                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = page.get_text()
                    if text:
                        text_parts.append(text)

                doc.close()

            full_text = "\n\n".join(text_parts)
            return normalize_korean(full_text)

        except Exception as e:
            logger.error(f"Error extracting text from PDF {pdf_path}: {e}")
            return ""

    def extract_text_from_bytes(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes"""
        fitz = self._get_fitz()

        try:
            with self._suppress_stderr():
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                text_parts = []

                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = page.get_text()
                    if text:
                        text_parts.append(text)

                doc.close()

            full_text = "\n\n".join(text_parts)
            return normalize_korean(full_text)

        except Exception as e:
            logger.error(f"Error extracting text from PDF bytes: {e}")
            return ""

    def extract_metadata(self, pdf_path: Union[str, Path]) -> dict:
        """Extract metadata from a PDF file"""
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return {}

        try:
            with self._suppress_stderr():
                doc = fitz.open(str(pdf_path))
                metadata = doc.metadata or {}
                doc.close()

            # Clean up metadata
            cleaned = {}
            for key, value in metadata.items():
                if value:
                    cleaned[key] = value

            return cleaned

        except Exception as e:
            logger.error(f"Error extracting metadata from PDF {pdf_path}: {e}")
            return {}

    def get_page_count(self, pdf_path: Union[str, Path]) -> int:
        """Get number of pages in PDF"""
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return 0

        try:
            with self._suppress_stderr():
                doc = fitz.open(str(pdf_path))
                count = len(doc)
                doc.close()
            return count
        except Exception:
            return 0

    def extract(self, pdf_path: Union[str, Path]) -> dict:
        """Extract all content from PDF"""
        pdf_path = Path(pdf_path)

        return {
            "text": self.extract_text(pdf_path),
            "metadata": self.extract_metadata(pdf_path),
            "page_count": self.get_page_count(pdf_path),
            "file_path": str(pdf_path),
        }

    def extract_pages(self, pdf_path: Union[str, Path]) -> list[str]:
        """Extract text from each page separately"""
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return []

        try:
            with self._suppress_stderr():
                doc = fitz.open(str(pdf_path))
                pages = []

                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = page.get_text()
                    pages.append(normalize_korean(text) if text else "")

                doc.close()
            return pages

        except Exception as e:
            logger.error(f"Error extracting pages from PDF {pdf_path}: {e}")
            return []

    def search_text(self, pdf_path: Union[str, Path], query: str) -> list[dict]:
        """Search for text in PDF and return locations"""
        fitz = self._get_fitz()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return []

        results = []

        try:
            with self._suppress_stderr():
                doc = fitz.open(str(pdf_path))

                for page_num in range(len(doc)):
                    page = doc[page_num]
                    matches = page.search_for(query)

                    for rect in matches:
                        results.append({
                            "page": page_num + 1,
                            "x": rect.x0,
                            "y": rect.y0,
                            "width": rect.width,
                            "height": rect.height,
                        })

                doc.close()
            return results

        except Exception as e:
            logger.error(f"Error searching PDF {pdf_path}: {e}")
            return []
