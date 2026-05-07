"""HTML content extractor"""

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment

from ..utils.korean import normalize_korean
from ..utils.logging import get_logger

logger = get_logger("extractors.html")


# Tags to remove entirely
REMOVE_TAGS = [
    "script",
    "style",
    "noscript",
    "iframe",
    "embed",
    "object",
    "svg",
    "canvas",
    "video",
    "audio",
    "map",
    "template",
]

# Tags that usually contain navigation/boilerplate
BOILERPLATE_TAGS = [
    "nav",
    "header",
    "footer",
    "aside",
]

# Classes/IDs that suggest navigation or boilerplate
BOILERPLATE_PATTERNS = [
    r"nav",
    r"menu",
    r"sidebar",
    r"footer",
    r"header",
    r"breadcrumb",
    r"pagination",
    r"widget",
    r"banner",
    r"advertisement",
    r"social",
    r"comment",
    r"related",
]


class HTMLExtractor:
    """Extract text and links from HTML"""

    def __init__(self, remove_boilerplate: bool = True):
        self.remove_boilerplate = remove_boilerplate
        self._boilerplate_pattern = re.compile(
            "|".join(BOILERPLATE_PATTERNS),
            re.IGNORECASE
        )

    def _is_boilerplate(self, tag) -> bool:
        """Check if a tag is likely boilerplate"""
        if not self.remove_boilerplate:
            return False

        # Check tag name
        if tag.name in BOILERPLATE_TAGS:
            return True

        # Check class and id
        classes = tag.get("class", [])
        if isinstance(classes, str):
            classes = [classes]

        tag_id = tag.get("id", "")

        for class_name in classes:
            if self._boilerplate_pattern.search(class_name):
                return True

        if tag_id and self._boilerplate_pattern.search(tag_id):
            return True

        return False

    def _clean_soup(self, soup: BeautifulSoup) -> BeautifulSoup:
        """Remove unwanted elements from soup"""
        # Remove comments
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # Remove script, style, etc.
        for tag_name in REMOVE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Remove boilerplate
        if self.remove_boilerplate:
            for tag in soup.find_all(self._is_boilerplate):
                tag.decompose()

        return soup

    def extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract page title"""
        # Try <title> tag
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            if title:
                return normalize_korean(title)

        # Try <h1>
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
            if title:
                return normalize_korean(title)

        # Try og:title
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return normalize_korean(og_title["content"])

        return None

    def extract_text(self, soup: BeautifulSoup) -> str:
        """Extract main text content"""
        # Try to find main content area
        main_content = None

        # Common content containers
        content_selectors = [
            ("main", {}),
            ("article", {}),
            ("div", {"class": re.compile(r"content|main|article|post", re.I)}),
            ("div", {"id": re.compile(r"content|main|article|post", re.I)}),
            ("div", {"role": "main"}),
        ]

        for tag_name, attrs in content_selectors:
            main_content = soup.find(tag_name, attrs)
            if main_content:
                break

        # Fall back to body
        if not main_content:
            main_content = soup.find("body")

        if not main_content:
            main_content = soup

        # Extract text
        text = main_content.get_text(separator="\n", strip=True)

        # Normalize
        text = normalize_korean(text)

        return text

    def extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """Extract all links from the page"""
        links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]

            # Skip empty, javascript, mailto links
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue

            # Resolve relative URLs
            full_url = urljoin(base_url, href)

            # Parse and normalize
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue

            # Remove fragment
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                normalized += f"?{parsed.query}"

            # Deduplicate
            if normalized not in seen:
                seen.add(normalized)
                links.append(normalized)

        return links

    def extract_document_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """Extract links to downloadable documents"""
        doc_extensions = (".pdf", ".doc", ".docx", ".hwp", ".hwpx", ".xls", ".xlsx", ".ppt", ".pptx")
        links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)

            # Check if it's a document link
            parsed = urlparse(full_url)
            if parsed.path.lower().endswith(doc_extensions):
                normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if parsed.query:
                    normalized += f"?{parsed.query}"

                if normalized not in seen:
                    seen.add(normalized)
                    links.append(normalized)

        return links

    def extract_metadata(self, soup: BeautifulSoup) -> dict:
        """Extract page metadata"""
        metadata = {}

        # Description
        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            metadata["description"] = desc["content"]

        # Keywords
        keywords = soup.find("meta", attrs={"name": "keywords"})
        if keywords and keywords.get("content"):
            metadata["keywords"] = keywords["content"]

        # Open Graph
        for og in soup.find_all("meta", property=re.compile(r"^og:")):
            prop = og.get("property", "").replace("og:", "")
            content = og.get("content")
            if prop and content:
                metadata[f"og_{prop}"] = content

        return metadata

    def extract(self, html: str, base_url: str) -> dict:
        """Extract all content from HTML"""
        soup = BeautifulSoup(html, "lxml")

        # Get title before cleaning
        title = self.extract_title(soup)

        # Get metadata before cleaning
        metadata = self.extract_metadata(soup)

        # Get links before cleaning (includes nav links)
        all_links = self.extract_links(soup, base_url)
        doc_links = self.extract_document_links(soup, base_url)

        # Clean soup for text extraction
        soup = self._clean_soup(soup)

        # Extract text
        text = self.extract_text(soup)

        return {
            "title": title,
            "text": text,
            "links": all_links,
            "document_links": doc_links,
            "metadata": metadata,
        }
