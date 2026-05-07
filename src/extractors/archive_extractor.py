"""Archive file extractor for handling compressed admission documents.

Supports:
- ZIP files
- RAR files (if rarfile is installed)
- 7z files (if py7zr is installed)
- ALZ/EGG files (Korean formats, limited support)
"""

import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator, Optional

from ..utils.logging import get_logger

logger = get_logger("extractors.archive")


class ArchiveExtractor:
    """Extract files from archive formats."""

    def __init__(self, extract_dir: Optional[Path] = None):
        """Initialize archive extractor.

        Args:
            extract_dir: Directory to extract files to. If None, uses temp directory.
        """
        self.extract_dir = extract_dir

    def _get_extract_path(self, archive_path: Path) -> Path:
        """Get extraction path for an archive."""
        if self.extract_dir:
            return self.extract_dir / archive_path.stem
        return Path(tempfile.mkdtemp(prefix=f"archive_{archive_path.stem}_"))

    def is_archive(self, file_path: Path) -> bool:
        """Check if file is a supported archive."""
        ext = file_path.suffix.lower()
        return ext in {'.zip', '.rar', '.7z', '.alz', '.egg'}

    def extract_zip(self, archive_path: Path, extract_to: Optional[Path] = None) -> list[Path]:
        """Extract ZIP archive.

        Args:
            archive_path: Path to ZIP file
            extract_to: Optional extraction directory

        Returns:
            List of extracted file paths
        """
        archive_path = Path(archive_path)
        extract_to = extract_to or self._get_extract_path(archive_path)
        extract_to.mkdir(parents=True, exist_ok=True)

        extracted_files = []

        try:
            with zipfile.ZipFile(archive_path, 'r') as zf:
                # Handle Korean filename encoding issues
                for info in zf.infolist():
                    # Try to decode filename properly
                    try:
                        # Try CP437 -> UTF-8 (common for Korean in ZIP)
                        filename = info.filename.encode('cp437').decode('euc-kr')
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        try:
                            filename = info.filename.encode('cp437').decode('utf-8')
                        except (UnicodeDecodeError, UnicodeEncodeError):
                            filename = info.filename

                    # Skip directories and hidden files
                    if filename.endswith('/') or filename.startswith('__MACOSX'):
                        continue

                    # Extract file
                    try:
                        # Read content
                        content = zf.read(info.filename)

                        # Create safe path
                        safe_name = Path(filename).name  # Get just filename, no path traversal
                        output_path = extract_to / safe_name

                        # Handle duplicates
                        counter = 1
                        base_name = output_path.stem
                        ext = output_path.suffix
                        while output_path.exists():
                            output_path = extract_to / f"{base_name}_{counter}{ext}"
                            counter += 1

                        output_path.write_bytes(content)
                        extracted_files.append(output_path)
                        logger.debug(f"Extracted: {output_path}")

                    except Exception as e:
                        logger.warning(f"Failed to extract {filename}: {e}")

        except zipfile.BadZipFile:
            logger.error(f"Invalid ZIP file: {archive_path}")
        except Exception as e:
            logger.error(f"Error extracting ZIP {archive_path}: {e}")

        return extracted_files

    def extract_rar(self, archive_path: Path, extract_to: Optional[Path] = None) -> list[Path]:
        """Extract RAR archive (requires rarfile package).

        Args:
            archive_path: Path to RAR file
            extract_to: Optional extraction directory

        Returns:
            List of extracted file paths
        """
        try:
            import rarfile
        except ImportError:
            logger.warning("rarfile package not installed. Cannot extract RAR files.")
            return []

        archive_path = Path(archive_path)
        extract_to = extract_to or self._get_extract_path(archive_path)
        extract_to.mkdir(parents=True, exist_ok=True)

        extracted_files = []

        try:
            with rarfile.RarFile(archive_path, 'r') as rf:
                for info in rf.infolist():
                    if info.is_dir():
                        continue

                    try:
                        filename = info.filename
                        safe_name = Path(filename).name
                        output_path = extract_to / safe_name

                        # Handle duplicates
                        counter = 1
                        base_name = output_path.stem
                        ext = output_path.suffix
                        while output_path.exists():
                            output_path = extract_to / f"{base_name}_{counter}{ext}"
                            counter += 1

                        rf.extract(info, extract_to)

                        # Move to flat structure if needed
                        actual_path = extract_to / filename
                        if actual_path != output_path and actual_path.exists():
                            shutil.move(str(actual_path), str(output_path))

                        extracted_files.append(output_path)
                        logger.debug(f"Extracted: {output_path}")

                    except Exception as e:
                        logger.warning(f"Failed to extract {info.filename}: {e}")

        except Exception as e:
            logger.error(f"Error extracting RAR {archive_path}: {e}")

        return extracted_files

    def extract_7z(self, archive_path: Path, extract_to: Optional[Path] = None) -> list[Path]:
        """Extract 7z archive (requires py7zr package).

        Args:
            archive_path: Path to 7z file
            extract_to: Optional extraction directory

        Returns:
            List of extracted file paths
        """
        try:
            import py7zr
        except ImportError:
            logger.warning("py7zr package not installed. Cannot extract 7z files.")
            return []

        archive_path = Path(archive_path)
        extract_to = extract_to or self._get_extract_path(archive_path)
        extract_to.mkdir(parents=True, exist_ok=True)

        extracted_files = []

        try:
            with py7zr.SevenZipFile(archive_path, 'r') as szf:
                szf.extractall(extract_to)

                # Collect extracted files
                for root, _, files in os.walk(extract_to):
                    for file in files:
                        file_path = Path(root) / file
                        extracted_files.append(file_path)
                        logger.debug(f"Extracted: {file_path}")

        except Exception as e:
            logger.error(f"Error extracting 7z {archive_path}: {e}")

        return extracted_files

    def extract(self, archive_path: Path, extract_to: Optional[Path] = None) -> list[Path]:
        """Extract archive based on file type.

        Args:
            archive_path: Path to archive file
            extract_to: Optional extraction directory

        Returns:
            List of extracted file paths
        """
        archive_path = Path(archive_path)
        ext = archive_path.suffix.lower()

        if ext == '.zip':
            return self.extract_zip(archive_path, extract_to)
        elif ext == '.rar':
            return self.extract_rar(archive_path, extract_to)
        elif ext == '.7z':
            return self.extract_7z(archive_path, extract_to)
        elif ext in {'.alz', '.egg'}:
            logger.warning(f"ALZ/EGG format not fully supported: {archive_path}")
            # Could try using external tool or library
            return []
        else:
            logger.warning(f"Unknown archive format: {ext}")
            return []

    def extract_and_filter(
        self,
        archive_path: Path,
        extensions: Optional[list[str]] = None,
        extract_to: Optional[Path] = None,
    ) -> list[Path]:
        """Extract archive and filter by file extensions.

        Args:
            archive_path: Path to archive file
            extensions: List of extensions to keep (e.g., ['.pdf', '.hwp'])
            extract_to: Optional extraction directory

        Returns:
            List of extracted file paths matching extensions
        """
        extracted = self.extract(archive_path, extract_to)

        if not extensions:
            return extracted

        # Normalize extensions
        extensions = [ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in extensions]

        filtered = []
        for file_path in extracted:
            if file_path.suffix.lower() in extensions:
                filtered.append(file_path)
            else:
                # Remove non-matching files
                try:
                    file_path.unlink()
                except Exception:
                    pass

        return filtered

    def iter_archive_contents(self, archive_path: Path) -> Iterator[tuple[str, bytes]]:
        """Iterate over archive contents without extracting to disk.

        Yields:
            Tuples of (filename, content_bytes)
        """
        archive_path = Path(archive_path)
        ext = archive_path.suffix.lower()

        if ext == '.zip':
            try:
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        try:
                            # Try to decode filename
                            try:
                                filename = info.filename.encode('cp437').decode('euc-kr')
                            except (UnicodeDecodeError, UnicodeEncodeError):
                                filename = info.filename

                            content = zf.read(info.filename)
                            yield (filename, content)
                        except Exception as e:
                            logger.warning(f"Failed to read {info.filename}: {e}")
            except Exception as e:
                logger.error(f"Error reading ZIP {archive_path}: {e}")
        else:
            # For other formats, extract temporarily
            extracted = self.extract(archive_path)
            for file_path in extracted:
                try:
                    content = file_path.read_bytes()
                    yield (file_path.name, content)
                except Exception as e:
                    logger.warning(f"Failed to read {file_path}: {e}")


def extract_archive(archive_path: str, extract_to: Optional[str] = None) -> list[str]:
    """Convenience function to extract an archive.

    Args:
        archive_path: Path to archive file
        extract_to: Optional extraction directory

    Returns:
        List of extracted file paths as strings
    """
    extractor = ArchiveExtractor()
    paths = extractor.extract(
        Path(archive_path),
        Path(extract_to) if extract_to else None
    )
    return [str(p) for p in paths]


def extract_admission_documents(
    archive_path: str,
    extract_to: Optional[str] = None
) -> list[str]:
    """Extract only admission-related document types from archive.

    Args:
        archive_path: Path to archive file
        extract_to: Optional extraction directory

    Returns:
        List of extracted document file paths
    """
    extractor = ArchiveExtractor()
    admission_extensions = ['.pdf', '.hwp', '.hwpx', '.doc', '.docx', '.xls', '.xlsx']

    paths = extractor.extract_and_filter(
        Path(archive_path),
        extensions=admission_extensions,
        extract_to=Path(extract_to) if extract_to else None
    )
    return [str(p) for p in paths]
