"""Korean text utilities"""

import re
import unicodedata
from typing import Optional

import chardet


def detect_encoding(content: bytes) -> str:
    """Detect the encoding of byte content, with Korean encoding preference"""
    result = chardet.detect(content)
    encoding = result.get("encoding", "utf-8")

    # Common Korean encodings
    if encoding and encoding.lower() in ["euc-kr", "cp949", "iso-8859-1"]:
        # Try EUC-KR first for Korean sites
        try:
            content.decode("euc-kr")
            return "euc-kr"
        except UnicodeDecodeError:
            pass

    return encoding or "utf-8"


def normalize_korean(text: str) -> str:
    """Normalize Korean text for consistent processing"""
    # Unicode normalization (NFC is standard for Korean)
    text = unicodedata.normalize("NFC", text)

    # Remove excessive whitespace while preserving structure
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def contains_korean(text: str) -> bool:
    """Check if text contains Korean characters"""
    # Korean Unicode ranges: Hangul Syllables, Jamo, Compatibility Jamo
    korean_pattern = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]")
    return bool(korean_pattern.search(text))


def extract_korean_text(text: str) -> str:
    """Extract only Korean text and basic punctuation"""
    # Keep Korean, numbers, basic punctuation, and whitespace
    pattern = re.compile(r"[^\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F0-9\s.,!?():\-]")
    return pattern.sub("", text)


def decode_safely(content: bytes, encoding: Optional[str] = None) -> str:
    """Safely decode bytes to string with fallback"""
    if encoding is None:
        encoding = detect_encoding(content)

    encodings_to_try = [encoding, "utf-8", "euc-kr", "cp949", "latin-1"]
    seen = set()

    for enc in encodings_to_try:
        if enc in seen or not enc:
            continue
        seen.add(enc)
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Last resort: decode with replacement
    return content.decode("utf-8", errors="replace")
