"""Download 입시결과 (admission results) PDFs for all universities.

Phase 1: CDN direct download from negagea.net (fast, concurrent)
Phase 2: Google search fallback for universities not found on CDN
"""

import asyncio
import io
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

console = Console()

# ── Constants ──────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
HTML_PATH = BASE_DIR / "adiga_main.html"
RESULTS_DIR = BASE_DIR / "data" / "results"
REPORT_PATH = BASE_DIR / "data" / "results_report.json"

# Try these combinations for each university
YEARS = ["2026", "2025", "2024"]
TYPES = ["수시", "정시"]
CYCLES = ["2025"]

MAX_CONCURRENT = 10
TIMEOUT = 30


# ── Parse universities ─────────────────────────────────────

def parse_universities(html_path: Path) -> list[dict]:
    """Parse university codes and names from adiga_main.html."""
    html = html_path.read_text(encoding="utf-8")

    # Pattern: <input ... value="CODE"/> followed by <label ...> NAME </label>
    pattern = re.compile(
        r'<input[^>]*class="univGroupInput"[^>]*value="(\d+)"[^>]*/>\s*'
        r'<label[^>]*>\s*(.+?)\s*<strong>',
        re.DOTALL,
    )

    universities = []
    seen_names = set()

    for match in pattern.finditer(html):
        code = match.group(1)
        raw_name = match.group(2).strip()

        # Clean: strip [본교], [분교], [제N캠퍼스] suffixes
        base_name = re.sub(r"\[.*?\]$", "", raw_name).strip()

        if base_name not in seen_names:
            seen_names.add(base_name)
            universities.append({"code": code, "raw_name": raw_name, "name": base_name})

    console.print(f"[bold]Parsed {len(universities)} unique universities[/bold]")
    return universities


# CDN name variants: some universities use campus-specific names on CDN
# that differ from the base name in adiga_main.html
CDN_NAME_VARIANTS = {
    "단국대학교": ["단국대학교(죽전)", "단국대학교(천안)"],
    "홍익대학교": ["홍익대학교(서울)", "홍익대학교(세종)"],
}


# ── CDN download ───────────────────────────────────────────

def build_cdn_urls(univ_name: str) -> list[tuple[str, str]]:
    """Build all CDN URL variants for a university. Returns (url, filename) pairs.

    Uses raw Korean characters in URLs — httpx handles percent-encoding internally.
    """
    names_to_try = [univ_name] + CDN_NAME_VARIANTS.get(univ_name, [])
    urls = []
    for name in names_to_try:
        for cycle in CYCLES:
            for year in YEARS:
                for type_ in TYPES:
                    filename = f"{name}_{year}학년도_{type_}입시결과.pdf"
                    url = (
                        f"https://cdn013.negagea.net/dgsmidc/omr/seoul/web/"
                        f"univ_info{cycle}/{name}/{filename}"
                    )
                    urls.append((url, filename))

    return urls


async def download_one(
    client: httpx.AsyncClient,
    url: str,
    save_path: Path,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Download a single file. Returns True if successful."""
    async with semaphore:
        try:
            # HEAD first to check existence
            head = await client.head(url, timeout=TIMEOUT)
            if head.status_code != 200:
                return False

            # GET the file
            resp = await client.get(url, timeout=TIMEOUT)
            if resp.status_code != 200:
                return False

            # Verify it's actually a PDF (check magic bytes)
            if not resp.content[:5].startswith(b"%PDF"):
                return False

            content_length = len(resp.content)
            if content_length < 1000:  # Too small to be a real PDF
                return False

            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(resp.content)
            return True

        except (httpx.TimeoutException, httpx.HTTPError, Exception):
            return False


async def cdn_download_university(
    client: httpx.AsyncClient,
    univ: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Download all available results for one university from CDN."""
    name = univ["name"]
    urls = build_cdn_urls(name)
    downloaded = []

    for url, filename in urls:
        save_path = RESULTS_DIR / name / filename
        if save_path.exists():
            downloaded.append(filename)
            continue

        ok = await download_one(client, url, save_path, semaphore)
        if ok:
            downloaded.append(filename)

    return {
        "name": name,
        "code": univ["code"],
        "cdn_files": downloaded,
        "cdn_count": len(downloaded),
    }


async def phase1_cdn(universities: list[dict]) -> list[dict]:
    """Phase 1: Download from CDN for all universities."""
    console.print("\n[bold blue]Phase 1: CDN Direct Download[/bold blue]")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=False,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        },
    ) as client:
        tasks = [
            cdn_download_university(client, univ, semaphore)
            for univ in universities
        ]

        results = []
        done = 0
        total = len(tasks)

        for coro in asyncio.as_completed(tasks):
            result = await coro
            done += 1
            count = result["cdn_count"]
            status = f"[green]{count} files[/green]" if count > 0 else "[dim]0[/dim]"
            console.print(
                f"  [{done}/{total}] {result['name']}: {status}"
            )
            results.append(result)

    # Sort by name for consistent ordering
    results.sort(key=lambda r: r["name"])

    cdn_total = sum(r["cdn_count"] for r in results)
    cdn_unis = sum(1 for r in results if r["cdn_count"] > 0)
    console.print(
        f"\n[bold green]CDN: {cdn_total} files from {cdn_unis}/{len(results)} universities[/bold green]"
    )

    return results


# ── Google search fallback ─────────────────────────────────

DOWNLOAD_EXTS = {".pdf", ".hwp", ".hwpx", ".docx", ".doc", ".xlsx", ".xls", ".jpg", ".jpeg", ".png"}
RELEVANCE_KEYWORDS = {"입시결과", "입학결과", "입시", "결과", "경쟁률", "합격", "커트라인", "모집결과"}
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _is_download_link(href: str) -> bool:
    """Check if a URL points to a downloadable file."""
    if not href:
        return False
    parsed = urlparse(href)
    path_lower = unquote(parsed.path).lower()
    # Check extension
    for ext in DOWNLOAD_EXTS:
        if path_lower.endswith(ext):
            return True
    # Some sites use query params for downloads (e.g. ?file=xxx.pdf)
    query_lower = unquote(parsed.query).lower()
    for ext in DOWNLOAD_EXTS:
        if ext in query_lower:
            return True
    # Common download URL patterns
    if any(kw in path_lower for kw in ["download", "filedown", "file_down", "attach"]):
        return True
    return False


def _is_relevant_link(href: str, text: str, univ_name: str) -> bool:
    """Check if a download link is relevant to admission results."""
    combined = unquote(href).lower() + " " + text.lower()
    # Must have at least one relevance keyword
    return any(kw in combined for kw in RELEVANCE_KEYWORDS)


def _get_file_ext(url: str, content: bytes, content_type: str) -> str:
    """Determine file extension from URL, content-type, or magic bytes."""
    # Try URL path first
    path = unquote(urlparse(url).path).lower()
    for ext in DOWNLOAD_EXTS:
        if path.endswith(ext):
            return ext

    # Try content-type header
    ct = content_type.lower()
    if "pdf" in ct:
        return ".pdf"
    if "hwp" in ct:
        return ".hwp"
    if "word" in ct or "docx" in ct:
        return ".docx"
    if "spreadsheet" in ct or "excel" in ct or "xlsx" in ct:
        return ".xlsx"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"

    # Try magic bytes
    if content[:5].startswith(b"%PDF"):
        return ".pdf"
    if content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # OLE2 (HWP/DOC/XLS)
        return ".hwp"  # Assume HWP for Korean context
    if content[:4] == b"PK\x03\x04":  # ZIP-based (DOCX/XLSX/HWPX)
        return ".docx"
    if content[:3] == b"\xff\xd8\xff":  # JPEG
        return ".jpg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":  # PNG
        return ".png"

    return ".pdf"  # Default assumption


def _extract_cd_filename(content_disposition: str) -> str | None:
    """Extract a filename from a Content-Disposition header value.

    Handles both the RFC 5987 ``filename*=UTF-8''...`` form (percent-encoded)
    and the plain ``filename="..."`` form used by older Korean web servers.
    """
    # RFC 5987 extended form (preferred, unambiguous encoding)
    m = re.search(r"filename\*\s*=\s*([^;]+)", content_disposition, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        if val.upper().startswith("UTF-8''"):
            return unquote(val[7:], encoding="utf-8").strip()

    # Plain filename= form
    m = re.search(r'filename\s*=\s*["\']?([^"\';\r\n]+)["\']?', content_disposition, re.IGNORECASE)
    if m:
        fname = m.group(1).strip().strip("\"'")
        return unquote(fname).strip() if fname else None

    return None


def _make_filename(url: str, content: bytes, content_type: str, index: int, prefix: str = "google",
                   content_disposition: str = "") -> str:
    """Extract or generate a filename for a downloaded file.

    Priority:
    1. Content-Disposition header (most reliable for server-renamed downloads)
    2. URL path last segment (only when the extension is a recognized document type)
    3. Generated fallback: 입시결과_{prefix}_{index}{ext}
    """
    # 1. Content-Disposition
    if content_disposition:
        cd_fname = _extract_cd_filename(content_disposition)
        if cd_fname and "." in cd_fname and len(cd_fname) < 200:
            ext = "." + cd_fname.rsplit(".", 1)[-1].lower()
            if ext in DOWNLOAD_EXTS:
                return cd_fname

    # 2. URL path segment — only accept if it carries a recognized document extension.
    #    Paths like "/download.do" or "/FileDownload" have no useful extension.
    path = urlparse(url).path
    url_filename = unquote(path.split("/")[-1]).strip()
    if url_filename and "." in url_filename and len(url_filename) < 200:
        ext = "." + url_filename.rsplit(".", 1)[-1].lower()
        if ext in DOWNLOAD_EXTS:
            return url_filename

    # 3. Generate one from content-type / magic bytes
    ext = _get_file_ext(url, content, content_type)
    return f"입시결과_{prefix}_{index}{ext}"


def _scrape_page_for_downloads(page_url: str, univ_name: str) -> list[str]:
    """Visit a page and extract relevant download links."""
    try:
        resp = httpx.get(
            page_url, timeout=20, follow_redirects=True, verify=False,
            headers=HTTP_HEADERS,
        )
        if resp.status_code != 200:
            return []
        ct = resp.headers.get("content-type", "")
        if "html" not in ct.lower():
            return []
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    found_urls = []

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        text = tag.get_text(strip=True)

        # Make absolute URL
        abs_url = urljoin(page_url, href)

        if _is_download_link(href) or _is_download_link(abs_url):
            if _is_relevant_link(abs_url, text, univ_name):
                if abs_url not in found_urls:
                    found_urls.append(abs_url)

    return found_urls


def _download_file(url: str, univ_name: str, index: int, prefix: str = "google") -> str | None:
    """Download a single file. Returns filename if successful, None otherwise."""
    try:
        resp = httpx.get(
            url, timeout=30, follow_redirects=True, verify=False,
            headers=HTTP_HEADERS,
        )
        if resp.status_code != 200:
            return None
        if len(resp.content) < 1000:
            return None

        content_type = resp.headers.get("content-type", "")
        content_disposition = resp.headers.get("content-disposition", "")

        # Skip HTML responses (some download URLs redirect to login pages)
        if "html" in content_type.lower() and not resp.content[:5].startswith(b"%PDF"):
            return None

        filename = _make_filename(url, resp.content, content_type, index, prefix, content_disposition)
        save_path = RESULTS_DIR / univ_name / filename

        # Skip if already exists
        if save_path.exists():
            return filename

        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(resp.content)
        return filename

    except Exception:
        return None


def _is_direct_file(url: str) -> bool:
    """HEAD-check if a URL is a direct file download (PDF/HWP/DOCX/Excel/etc)."""
    try:
        resp = httpx.head(
            url, timeout=15, follow_redirects=True, verify=False,
            headers=HTTP_HEADERS,
        )
        if resp.status_code != 200:
            return False
        ct = resp.headers.get("content-type", "").lower()
        return any(t in ct for t in [
            "pdf", "octet-stream", "excel", "spreadsheet",
            "hwp", "msword", "wordprocessing", "hancom",
        ])
    except Exception:
        return False


def phase2_search(results: list[dict]) -> list[dict]:
    """Phase 2: DuckDuckGo search for 입시결과 files (PDF/HWP/DOCX).

    Strategy:
    1. Search DuckDuckGo for '{university} 입시결과 {year} pdf'
    2. Check if result URLs are direct files (URL pattern + HEAD Content-Type)
    3. Also scrape result pages for download links
    4. Download anything found
    """
    missing = [r for r in results if r["cdn_count"] == 0]
    if not missing:
        console.print("\n[bold green]Phase 2: Skipped (all universities found on CDN)[/bold green]")
        return results

    console.print(
        f"\n[bold blue]Phase 2: Web Search ({len(missing)} universities)[/bold blue]"
    )

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        console.print(
            "[yellow]duckduckgo-search not installed. "
            "Install with: pip install duckduckgo-search[/yellow]"
        )
        console.print("[yellow]Skipping Phase 2.[/yellow]")
        return results

    result_map = {r["name"]: r for r in results}
    ddgs = DDGS()

    for i, r in enumerate(missing):
        name = r["name"]
        search_files = []

        # Search for admission results
        queries = [
            f"{name} 입시결과 2025 pdf",
            f"{name} 입시결과 2026",
        ]

        # Collect (url, title) pairs for relevance checking
        all_hits: list[tuple[str, str]] = []
        for query in queries:
            console.print(f"  [{i+1}/{len(missing)}] Searching: {query}")
            try:
                hits = ddgs.text(query, region="kr-kr", max_results=8)
                for h in hits:
                    all_hits.append((h["href"], h.get("title", "")))
            except Exception as e:
                console.print(f"    [red]Search error: {e}[/red]")
            time.sleep(2)

        # Deduplicate by URL
        seen = set()
        unique_hits = []
        for url, title in all_hits:
            if url not in seen:
                seen.add(url)
                unique_hits.append((url, title))

        # Relevance check: URL or title must mention 입시결과-related keywords
        SEARCH_RELEVANCE = {"입시결과", "입학결과", "입결", "경쟁률", "합격", "커트라인", "모집결과", "등급컷"}

        def _is_relevant(url: str, title: str) -> bool:
            combined = unquote(url).lower() + " " + title.lower()
            return any(kw in combined for kw in SEARCH_RELEVANCE)

        # Classify URLs: direct file download vs HTML page
        download_urls = set()
        html_pages = []
        for url, title in unique_hits:
            if not _is_relevant(url, title):
                continue  # Skip irrelevant results
            if _is_download_link(url):
                download_urls.add(url)
            elif _is_direct_file(url):
                download_urls.add(url)
            else:
                html_pages.append(url)

        # Scrape HTML pages for download links (limit to 4 pages)
        for page_url in html_pages[:4]:
            scraped = _scrape_page_for_downloads(page_url, name)
            for u in scraped:
                download_urls.add(u)

        if download_urls:
            console.print(f"    Found {len(download_urls)} download links, downloading...")
            file_idx = 0
            for dl_url in list(download_urls)[:6]:
                fname = _download_file(dl_url, name, file_idx)
                if fname:
                    search_files.append(fname)
                    console.print(f"    [green]+ {fname}[/green]")
                    file_idx += 1

        if search_files:
            result_map[name].setdefault("google_files", []).extend(search_files)
            console.print(
                f"    [bold green]{len(search_files)} files downloaded[/bold green]"
            )
        else:
            console.print(f"    [dim]No files found[/dim]")

    return results


# ── Phase 3: Playwright SPA scraping ──────────────────────

# Known admission portal pages for specific universities.
# Maps university name → URL of the admission results page (SPA or otherwise).
# Populate this as you discover portal URLs for universities missing from CDN/search.
KNOWN_PORTALS: dict[str, str] = {
    "창신대학교": "https://admission.cs.ac.kr/board/569/view?boardId=355&menuId=569",
    # Portals for missing Tier 3 universities (added 2026-03-10)
    "국민대학교": "https://admission.kookmin.ac.kr/onschedule/previousResult.php",
    "인하대학교": "https://admission.inha.ac.kr/cms/FR_BBS_CON/BoardView.do?MENU_ID=240&SITE_NO=2&BOARD_SEQ=1&BBS_SEQ=1264",
    "조선대학교": "https://i.chosun.ac.kr/Contents/A000000124",
    "국립부경대학교": "https://ipsi.pknu.ac.kr/iphak/web/board/page.do?menuID=10&boardID=8",
    "국립창원대학교": "https://ipsi.changwon.ac.kr",
    "울산대학교": "https://iphak.ulsan.ac.kr",
    "원광대학교": "https://ipsi.wku.ac.kr",
    "한동대학교": "https://iphak.handong.edu",
}


# Keywords matched against a board entry link's OWN visible text to decide whether
# to click through to its detail page.  Deliberately excludes generic words like
# "수시모집", "정시모집", "학년도" that appear in 모집요강 titles and sidebar nav links.
_CLICK_KEYWORDS = {
    "입시결과", "전형결과", "수시결과", "정시결과", "입학결과", "모집결과",
    "경쟁률", "평균등급", "평균 등급", "합격선", "충원", "입결",
}

# Stricter compound keywords for image files — avoids logos/banners.
# Single words like "결과" or "입시" are intentionally excluded.
_IMAGE_RESULT_KEYWORDS = {
    "입시결과", "전형결과", "수시결과", "정시결과", "입학결과", "모집결과", "경쟁률",
}

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif"}

# Extensions that should NEVER be downloaded even if content-type is octet-stream.
# Fonts, web assets, and media files are served as application/octet-stream on many
# CDNs and would otherwise be caught by the network interception.
_SKIP_EXTS = {
    ".woff", ".woff2", ".ttf", ".otf", ".eot",   # web fonts
    ".js", ".mjs", ".cjs", ".css", ".map",        # web assets
    ".ico", ".svg", ".webp",                       # icons / vector
    ".mp4", ".webm", ".ogg", ".mp3", ".wav",      # media
    ".zip", ".tar", ".gz", ".7z", ".rar",          # archives (handled via explicit link only)
}


async def _collect_from_url(
    page, url: str, univ_name: str
) -> tuple[list[str], "BeautifulSoup"]:
    """Navigate to *url* with Playwright, then collect download links.

    Uses two complementary strategies:
    • Network interception — document MIME types (PDF, HWP, Excel …) only.
      Images are excluded: <img> logos fire the same events and we have no
      link-text context to filter them here.
    • DOM parsing — <a href> links in the fully rendered HTML.
      Documents use the general relevance check; image-extension URLs require
      the stricter _IMAGE_RESULT_KEYWORDS to avoid logo anchors.

    Returns (download_urls, rendered_soup) so the caller can mine the soup for
    further subpage links without a second network round-trip.
    """
    intercepted: list[str] = []

    async def on_response(response) -> None:
        try:
            ct = response.headers.get("content-type", "").lower()
        except Exception:
            ct = ""
        resp_url = response.url
        # Skip web fonts, JS/CSS, media, and other non-document assets that are
        # frequently served as application/octet-stream on Korean university CDNs.
        url_path = urlparse(resp_url).path.lower()
        if any(url_path.endswith(ext) for ext in _SKIP_EXTS):
            return
        if any(t in ct for t in ["pdf", "octet-stream", "hwp", "hancom", "msword", "excel", "spreadsheet"]):
            if resp_url not in intercepted:
                intercepted.append(resp_url)
        elif _is_download_link(resp_url) and not any(
            ct.startswith(f"image/{t}") for t in ["jpeg", "png", "gif", "webp"]
        ):
            if resp_url not in intercepted:
                intercepted.append(resp_url)

    page.on("response", on_response)
    try:
        await page.goto(url, wait_until="networkidle", timeout=30_000)
    except Exception:
        pass
    await asyncio.sleep(2)
    try:
        page.remove_listener("response", on_response)
    except Exception:
        pass

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    dom_urls: list[str] = []

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        text = tag.get_text(strip=True)
        abs_url = urljoin(url, href)

        if not (_is_download_link(abs_url) or _is_download_link(href)):
            continue

        path_lower = unquote(urlparse(abs_url).path).lower()
        is_image = any(path_lower.endswith(ext) for ext in _IMAGE_EXTS)

        if is_image:
            combined = unquote(abs_url).lower() + " " + text.lower()
            if not any(kw in combined for kw in _IMAGE_RESULT_KEYWORDS):
                continue
        else:
            if not _is_relevant_link(abs_url, text, univ_name):
                # Fallback: check nearest row/list-item ancestor.
                # Attachment filenames like "첨부1.hwp" won't have keywords, but
                # their <tr> title cell ("2025 수시모집 전형결과 안내") will.
                context_text = ""
                for ancestor in tag.parents:
                    aname = getattr(ancestor, "name", "")
                    if aname in ("tr", "li", "dt", "article"):
                        context_text = ancestor.get_text(" ", strip=True)
                        break
                if not any(kw in context_text for kw in RELEVANCE_KEYWORDS):
                    continue

        if abs_url not in dom_urls:
            dom_urls.append(abs_url)

    return list(dict.fromkeys(intercepted + dom_urls)), soup


async def _find_result_entry_texts(page) -> list[str]:
    """Find visible text of <a> elements whose OWN text contains a _CLICK_KEYWORD.

    Matching against the link's own text (not an ancestor row) prevents sidebar
    navigation items (e.g. <h3>수시모집</h3> section links) from being selected.
    Works for both regular hrefs and javascript: links because it runs in the
    live browser DOM.

    Returns a deduplicated list of stripped link texts, capped at 100 chars each
    to avoid matching entire paragraphs accidentally embedded in <a> tags.
    """
    keywords_json = json.dumps(sorted(_CLICK_KEYWORDS))
    texts: list[str] = await page.evaluate(f"""
        () => {{
            const keywords = {keywords_json};
            const seen = new Set();
            const result = [];
            document.querySelectorAll('a').forEach(a => {{
                const text = (a.innerText || a.textContent || '').trim().slice(0, 100);
                if (!text || seen.has(text)) return;
                if (keywords.some(kw => text.includes(kw))) {{
                    seen.add(text);
                    result.push(text);
                }}
            }});
            return result;
        }}
    """) or []
    return texts


async def _playwright_render_links(page, portal_url: str, univ_name: str) -> list[str]:
    """Render the portal page, then click board entries that mention result keywords.

    Many university boards follow a two-level structure:
        list page  →  detail pages  →  download files

    Level 1 collects any download links directly visible on the portal page (some
    sites serve the files right there).

    Level 2 uses a unified click-based approach that works for both regular href
    links and javascript: form-submit links:
      1. While still on the portal page, collect the visible text of all <a>
         elements whose OWN text matches _CLICK_KEYWORDS.
      2. For each matched text, re-locate the element in the live DOM and click it.
      3. After navigation, reject external domains (e.g. uwayapply.com).
      4. Collect all download links from the detail page — no relevance filter
         needed since the entry was already keyword-filtered.
      5. Navigate back to the portal for the next entry.
    """
    MAX_ENTRIES = 6

    # Level 1 — the portal/list page itself
    download_urls, _ = await _collect_from_url(page, portal_url, univ_name)

    # Collect entry texts while still on the portal page
    entry_texts = await _find_result_entry_texts(page)
    if entry_texts:
        console.print(f"    Clicking {min(len(entry_texts), MAX_ENTRIES)} result entry/entries…")

    for entry_text in entry_texts[:MAX_ENTRIES]:
        # Return to portal so the JS context (fn_viewData etc.) is available
        if page.url != portal_url:
            try:
                await page.goto(portal_url, wait_until="networkidle", timeout=30_000)
                await asyncio.sleep(1)
            except Exception:
                break

        # Click the <a> whose text exactly matches entry_text
        try:
            async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
                await page.evaluate(
                    """(text) => {
                        const el = Array.from(document.querySelectorAll('a')).find(
                            a => (a.innerText || a.textContent || '').trim().slice(0, 100) === text
                        );
                        if (el) el.click();
                    }""",
                    entry_text,
                )
            await asyncio.sleep(2)
        except Exception:
            await asyncio.sleep(2)  # navigation may have already completed

        detail_url = page.url
        if detail_url == portal_url:
            continue  # click did not trigger navigation

        # Reject external sites (e.g. links to uwayapply.com or naver)
        if not _is_official_univ_domain(detail_url):
            continue

        # Collect every download link on the detail page.
        # The entry was keyword-filtered, so this page is a result page —
        # accept all file links (skip only irrelevant image files).
        html = await page.content()
        detail_soup = BeautifulSoup(html, "html.parser")
        for tag in detail_soup.find_all("a", href=True):
            href = tag["href"].strip()
            abs_url = urljoin(detail_url, href)
            if not (_is_download_link(abs_url) or _is_download_link(href)):
                continue
            path_lower = unquote(urlparse(abs_url).path).lower()
            is_image = any(path_lower.endswith(ext) for ext in _IMAGE_EXTS)
            if is_image:
                combined = unquote(abs_url).lower() + " " + tag.get_text(strip=True).lower()
                if not any(kw in combined for kw in _IMAGE_RESULT_KEYWORDS):
                    continue
            if abs_url not in download_urls:
                download_urls.append(abs_url)

        await asyncio.sleep(0.5)

    return download_urls


def _is_official_univ_domain(url: str) -> bool:
    """Return True only for official Korean university domains (*.ac.kr)."""
    host = urlparse(url).netloc.lower()
    # Strip port if present
    host = host.split(":")[0]
    return host.endswith(".ac.kr")


def _find_portal_url(univ_name: str, ddgs) -> str | None:
    """Return the admission results page URL for a university.

    Checks KNOWN_PORTALS first, then searches DuckDuckGo.
    Queries target the 전형결과/수시결과/정시결과 page directly rather than
    the main portal homepage, since the actual results are nested several
    levels deep in most admission portals.
    Only official university domains (*.ac.kr) are accepted — third-party
    sites like blog.naver.com, tistory.com, etc. are rejected.
    Direct file URLs are skipped (those are Phase 2 territory).
    """
    if univ_name in KNOWN_PORTALS:
        return KNOWN_PORTALS[univ_name]

    if ddgs is None:
        return None

    queries = [
        f"{univ_name} 전형결과",
        f"{univ_name} 수시 정시 입시결과",
        f"{univ_name} 입시결과 입학처",
    ]
    for query in queries:
        try:
            hits = ddgs.text(query, region="kr-kr", max_results=8)
            for h in hits:
                url = h["href"]
                title = h.get("title", "")
                if not _is_official_univ_domain(url):
                    continue  # Reject third-party sites (naver blog, tistory, …)
                if _is_download_link(url):
                    continue  # Phase 2 handles direct file links
                # Accept any official page whose URL or title mentions results
                combined = (url + " " + title).lower()
                if any(kw in combined for kw in RELEVANCE_KEYWORDS):
                    return url
        except Exception:
            pass
        time.sleep(2)

    return None


async def phase3_scrape(results: list[dict]) -> list[dict]:
    """Phase 3: Playwright-based scraping for JavaScript-rendered admission portals.

    Targets universities that yielded nothing in Phases 1 & 2.  Playwright renders
    SPA pages that plain HTTP scraping cannot, exposing embedded download links.

    Requires: pip install playwright && playwright install chromium
    """
    missing = [r for r in results if r["cdn_count"] == 0 and not r.get("google_files")]
    if not missing:
        console.print("\n[bold green]Phase 3: Skipped (all universities already found)[/bold green]")
        return results

    console.print(
        f"\n[bold blue]Phase 3: Playwright SPA Scraping ({len(missing)} universities)[/bold blue]"
    )

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        console.print(
            "[yellow]playwright not installed.[/yellow]\n"
            "[yellow]Install: pip install playwright && playwright install chromium[/yellow]\n"
            "[yellow]Skipping Phase 3.[/yellow]"
        )
        return results

    try:
        from duckduckgo_search import DDGS
        ddgs = DDGS()
    except ImportError:
        console.print("[yellow]duckduckgo-search not installed — only KNOWN_PORTALS will be used.[/yellow]")
        ddgs = None

    result_map = {r["name"]: r for r in results}

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            console.print(f"[red]Failed to launch Chromium: {e}[/red]")
            console.print("[yellow]Run: playwright install chromium[/yellow]")
            return results

        context = await browser.new_context(
            user_agent=HTTP_HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            ignore_https_errors=True,
        )

        for i, r in enumerate(missing):
            name = r["name"]
            console.print(f"  [{i+1}/{len(missing)}] {name}", end="")

            portal_url = _find_portal_url(name, ddgs)
            if not portal_url:
                console.print(" [dim]— no portal found[/dim]")
                continue
            console.print(f"\n    Portal: {portal_url}")

            page = await context.new_page()
            download_urls: list[str] = []
            try:
                download_urls = await _playwright_render_links(page, portal_url, name)
            except Exception as e:
                console.print(f"    [red]Render error: {e}[/red]")
            finally:
                await page.close()

            if not download_urls:
                console.print("    [dim]No download links found[/dim]")
                await asyncio.sleep(1)
                continue

            console.print(f"    Found {len(download_urls)} download link(s)")
            scrape_files: list[str] = []
            for idx, dl_url in enumerate(download_urls[:6]):
                fname = _download_file(dl_url, name, idx, prefix="scrape")
                if fname:
                    scrape_files.append(fname)
                    console.print(f"    [green]+ {fname}[/green]")

            if scrape_files:
                result_map[name]["scrape_files"] = scrape_files
                console.print(f"    [bold green]{len(scrape_files)} file(s) downloaded[/bold green]")
            else:
                console.print("    [dim]No files downloaded[/dim]")

            await asyncio.sleep(1)

        await browser.close()

    return results


# ── Report ─────────────────────────────────────────────────

def generate_report(results: list[dict]) -> None:
    """Save JSON report and print summary table."""
    # Enrich with totals
    for r in results:
        google_count = len(r.get("google_files", []))
        scrape_count = len(r.get("scrape_files", []))
        r["google_count"] = google_count
        r["scrape_count"] = scrape_count
        r["total_count"] = r["cdn_count"] + google_count + scrape_count

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Summary stats
    total_files = sum(r["total_count"] for r in results)
    cdn_files = sum(r["cdn_count"] for r in results)
    google_files = sum(r["google_count"] for r in results)
    scrape_files = sum(r["scrape_count"] for r in results)
    unis_with_files = sum(1 for r in results if r["total_count"] > 0)
    unis_without = sum(1 for r in results if r["total_count"] == 0)

    # Summary table
    table = Table(title="입시결과 Download Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total universities", str(len(results)))
    table.add_row("With results", f"[green]{unis_with_files}[/green]")
    table.add_row("Without results", f"[red]{unis_without}[/red]")
    table.add_row("CDN files", str(cdn_files))
    table.add_row("Google/search files", str(google_files))
    table.add_row("Scrape files", str(scrape_files))
    table.add_row("Total files", f"[bold]{total_files}[/bold]")

    console.print()
    console.print(table)
    console.print(f"\nReport saved to: {REPORT_PATH}")

    # List universities without results
    if unis_without > 0 and unis_without <= 50:
        console.print(f"\n[dim]Universities without results:[/dim]")
        for r in results:
            if r["total_count"] == 0:
                console.print(f"  [dim]- {r['name']}[/dim]")


# ── Main ───────────────────────────────────────────────────

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Download 입시결과 PDFs")
    parser.add_argument("--google", action="store_true", help="Enable web search fallback (slow)")
    parser.add_argument(
        "--scrape", action="store_true",
        help="Enable Playwright SPA scraping (requires: pip install playwright && playwright install chromium)",
    )
    args = parser.parse_args()

    console.print("[bold]입시결과 Download Script[/bold]\n")

    # Parse universities
    if not HTML_PATH.exists():
        console.print(f"[red]Error: {HTML_PATH} not found[/red]")
        return

    universities = parse_universities(HTML_PATH)
    if not universities:
        console.print("[red]No universities found in HTML[/red]")
        return

    # Phase 1: CDN
    results = await phase1_cdn(universities)

    # Phase 2: Web search fallback (opt-in)
    if args.google:
        results = phase2_search(results)

    # Phase 3: Playwright SPA scraping (opt-in)
    if args.scrape:
        results = await phase3_scrape(results)

    # Report
    generate_report(results)


if __name__ == "__main__":
    asyncio.run(main())
