"""Admission content filters for Korean university admission crawling.

Enhanced filters specifically for:
- 모집요강 (Admission guidelines)
- 입시결과 (Admission results)
- 수시전형/정시전형 (Early/Regular admission)
- 전형계획 (Admission plans)
"""

import re
from typing import Optional
from urllib.parse import urlparse, unquote

from ..utils.logging import get_logger

logger = get_logger("crawler.filters")


# =============================================================================
# Korean Admission Keywords - Comprehensive List
# =============================================================================

# Core admission terms (highest priority)
ADMISSION_KEYWORDS_CORE = [
    "모집요강",          # Admission guidelines (most important)
    "입시요강",          # Entrance exam guidelines
    "전형요강",          # Selection guidelines
    "입시결과",          # Admission results
    "입학결과",          # Admission results
    "전형결과",          # Selection results
    "합격자발표",        # Announcement of successful applicants
    "수시전형",          # Early admission
    "정시전형",          # Regular admission
    "수시모집",          # Early recruitment
    "정시모집",          # Regular recruitment
    "전형계획",          # Admission plan
    "모집계획",          # Recruitment plan
]

# Admission type terms
ADMISSION_KEYWORDS_TYPES = [
    "학생부종합",        # Comprehensive student record
    "학생부교과",        # Student record (subjects)
    "논술전형",          # Essay-based admission
    "실기전형",          # Practical exam admission
    "특기자전형",        # Special talent admission
    "정원외전형",        # Out-of-quota admission
    "정원내전형",        # In-quota admission
    "특별전형",          # Special admission
    "농어촌전형",        # Rural area admission
    "기회균형",          # Equal opportunity admission
    "사회배려",          # Social consideration
    "고른기회",          # Equal opportunity
    "지역균형",          # Regional balance
    "기초생활수급자",    # Basic livelihood recipients
    "차상위계층",        # Near-poverty class
    "국가보훈",          # National merit
    "장애인",            # Disabled persons
    "특성화고",          # Specialized high school
    "재직자전형",        # Working persons admission
    "성인학습자",        # Adult learners
    "외국인전형",        # International admission
    "재외국민",          # Overseas Koreans
    "북한이탈주민",      # North Korean defectors
]

# General admission terms
ADMISSION_KEYWORDS_GENERAL = [
    "입학",              # Admission
    "모집",              # Recruitment
    "전형",              # Selection/Screening
    "수시",              # Early admission
    "정시",              # Regular admission
    "편입",              # Transfer admission
    "편입학",            # Transfer admission
    "대학원",            # Graduate school
    "원서",              # Application
    "원서접수",          # Application submission
    "지원",              # Apply/Support
    "지원자격",          # Eligibility
    "합격",              # Acceptance
    "합격자",            # Successful applicants
    "예비합격",          # Preliminary acceptance
    "충원합격",          # Replacement acceptance
    "추가합격",          # Additional acceptance
    "등록",              # Registration
    "등록금",            # Tuition
    "장학",              # Scholarship
    "장학금",            # Scholarship fund
    "기숙사",            # Dormitory
    "학생부",            # Student record
    "생활기록부",        # School life record
    "면접",              # Interview
    "논술",              # Essay test
    "수능",              # CSAT (Korean SAT)
    "대학수학능력",      # College Scholastic Ability
    "내신",              # School grades
    "내신성적",          # Internal grades
    "입시",              # Entrance exam
    "신입생",            # New students
    "신입학",            # New admission
    "입학처",            # Admission office
    "입학본부",          # Admission headquarters
    "입학관리",          # Admission management
    "교차지원",          # Cross-application
    "복수지원",          # Multiple applications
    "중복지원",          # Duplicate application
    "추가모집",          # Additional recruitment
    "예비",              # Reserve/Preliminary
    "외국인",            # International
    "유학생",            # International students
    "학생선발",          # Student selection
    "선발인원",          # Number of selected
    "모집인원",          # Recruitment quota
    "경쟁률",            # Competition ratio
    "지원율",            # Application rate
    "최저학력",          # Minimum academic requirement
    "수능최저",          # CSAT minimum
    "전형일정",          # Admission schedule
    "전형료",            # Application fee
    "제출서류",          # Required documents
    "자기소개서",        # Personal statement
    "추천서",            # Recommendation letter
    "학교장추천",        # Principal recommendation
    "교사추천",          # Teacher recommendation
    "실기",              # Practical exam
    "적성",              # Aptitude
    "적성검사",          # Aptitude test
]

# Result and statistics terms
ADMISSION_KEYWORDS_RESULTS = [
    "입시결과",          # Admission results
    "전형결과",          # Selection results
    "합격선",            # Passing score line
    "합격컷",            # Passing cutoff
    "커트라인",          # Cutline
    "평균",              # Average
    "평균등급",          # Average grade
    "등급컷",            # Grade cutoff
    "백분위",            # Percentile
    "표준점수",          # Standard score
    "원점수",            # Raw score
    "환산점수",          # Converted score
    "반영비율",          # Reflection ratio
    "가중치",            # Weight
    "배점",              # Score allocation
    "충원율",            # Replacement rate
    "최초합격",          # Initial acceptance
    "최종등록",          # Final registration
]

# English admission-related keywords
ADMISSION_KEYWORDS_EN = [
    "admission",
    "admissions",
    "application",
    "apply",
    "enrollment",
    "enroll",
    "tuition",
    "scholarship",
    "dormitory",
    "residence",
    "international",
    "undergraduate",
    "graduate",
    "transfer",
    "prospective",
    "requirements",
    "deadline",
    "fee",
    "financial",
    "aid",
    "freshman",
    "selection",
    "quota",
    "guideline",
    "brochure",
    "bulletin",
]

# Combine all Korean keywords
ADMISSION_KEYWORDS_KO = (
    ADMISSION_KEYWORDS_CORE +
    ADMISSION_KEYWORDS_TYPES +
    ADMISSION_KEYWORDS_GENERAL +
    ADMISSION_KEYWORDS_RESULTS
)

# =============================================================================
# URL Patterns
# =============================================================================

# URL patterns that strongly suggest admission content
ADMISSION_URL_PATTERNS_STRONG = [
    r"모집요강",
    r"입시요강",
    r"전형요강",
    r"입시결과",
    r"합격자",
    r"수시모집",
    r"정시모집",
    r"수시전형",
    r"정시전형",
    r"전형계획",
    r"/ipsi/",            # 입시 romanization
    r"/iphak/",           # 입학 romanization
    r"/admission[s]?/",
    r"/apply/",
    r"/enroll/",
    r"/suip/",            # 수입 romanization (tuition)
    r"mojib",             # 모집 romanization
    r"jeonhyung",         # 전형 romanization
]

# URL patterns that suggest admission content
ADMISSION_URL_PATTERNS_MODERATE = [
    r"/입학/",
    r"/모집/",
    r"/전형/",
    r"/scholarship/",
    r"/janghak/",         # 장학 romanization
    r"/tuition/",
    r"/undergraduate/",
    r"/graduate/",
    r"/international/",
    r"/global/",
    r"/foreign/",
    r"/dormitory/",
    r"/기숙사/",
    r"susi",              # 수시 romanization
    r"jungsi",            # 정시 romanization
    r"jeongsi",           # 정시 romanization
]

# URL patterns to skip (not admission related)
SKIP_URL_PATTERNS = [
    r"/login",
    r"/signin",
    r"/sign-in",
    r"/auth",
    r"/oauth",
    r"/member",
    r"/mypage",
    r"/my-page",
    r"/cart",
    r"/order",
    r"/payment",
    r"/admin",
    r"/wp-admin",
    r"/feed",
    r"/rss",
    r"/sitemap",
    r"/robots\.txt",
    r"/api/",
    r"/ajax/",
    r"/cdn/",
    r"/static/",
    r"/assets/",
    r"/images?/",
    r"/img/",
    r"/css/",
    r"/js/",
    r"/fonts?/",
    r"\.css($|\?)",
    r"\.js($|\?)",
    r"\.ico($|\?)",
    r"\.png($|\?)",
    r"\.jpg($|\?)",
    r"\.jpeg($|\?)",
    r"\.gif($|\?)",
    r"\.svg($|\?)",
    r"\.webp($|\?)",
    r"\.woff",
    r"\.woff2",
    r"\.ttf",
    r"\.eot",
    r"#",  # Anchor links
    r"javascript:",
    r"mailto:",
    r"tel:",
]

# =============================================================================
# Document Extensions
# =============================================================================

# File extensions to download as documents (priority order)
DOCUMENT_EXTENSIONS_PRIMARY = [
    ".pdf",      # Most common for 모집요강
    ".hwp",      # Korean word processor (common)
    ".hwpx",     # New HWP format
]

DOCUMENT_EXTENSIONS_SECONDARY = [
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
]

DOCUMENT_EXTENSIONS_ARCHIVES = [
    ".zip",
    ".rar",
    ".7z",
    ".alz",      # Korean archive format
    ".egg",      # Korean archive format
]

DOCUMENT_EXTENSIONS = (
    DOCUMENT_EXTENSIONS_PRIMARY +
    DOCUMENT_EXTENSIONS_SECONDARY +
    DOCUMENT_EXTENSIONS_ARCHIVES
)


# =============================================================================
# Filename Patterns for Admission Documents
# =============================================================================

# Filename patterns that strongly indicate admission documents
ADMISSION_FILENAME_PATTERNS = [
    r"모집요강",
    r"입시요강",
    r"전형요강",
    r"입시결과",
    r"전형결과",
    r"합격자",
    r"수시.*모집",
    r"정시.*모집",
    r"202[4-9].*모집",      # Year + 모집
    r"202[4-9].*전형",      # Year + 전형
    r"202[4-9].*입시",      # Year + 입시
    r"202[4-9].*입학",      # Year + 입학
    r"신입생.*모집",
    r"신입학.*안내",
    r"admission.*guide",
    r"application.*form",
    r"brochure",
]


class AdmissionFilter:
    """Filter for identifying admission-related content.

    Optimized for Korean university admission documents including:
    - 모집요강 (Admission guidelines)
    - 입시결과 (Admission results)
    - 수시/정시전형 (Early/Regular admission)
    """

    def __init__(
        self,
        keywords_ko: Optional[list[str]] = None,
        keywords_en: Optional[list[str]] = None,
        min_score: float = 0.25,
    ):
        self.keywords_ko = keywords_ko or ADMISSION_KEYWORDS_KO
        self.keywords_en = keywords_en or ADMISSION_KEYWORDS_EN
        self.keywords_core = ADMISSION_KEYWORDS_CORE
        self.min_score = min_score

        # Compile patterns
        self._url_patterns_strong = [
            re.compile(p, re.IGNORECASE) for p in ADMISSION_URL_PATTERNS_STRONG
        ]
        self._url_patterns_moderate = [
            re.compile(p, re.IGNORECASE) for p in ADMISSION_URL_PATTERNS_MODERATE
        ]
        self._skip_patterns = [
            re.compile(p, re.IGNORECASE) for p in SKIP_URL_PATTERNS
        ]
        self._filename_patterns = [
            re.compile(p, re.IGNORECASE) for p in ADMISSION_FILENAME_PATTERNS
        ]

        # Build keyword regex - prioritize core keywords
        all_keywords = self.keywords_ko + self.keywords_en
        self._keyword_pattern = re.compile(
            r"|".join(re.escape(k) for k in all_keywords),
            re.IGNORECASE
        )

        # Core keywords pattern for high-priority matching
        self._core_keyword_pattern = re.compile(
            r"|".join(re.escape(k) for k in ADMISSION_KEYWORDS_CORE),
            re.IGNORECASE
        )

    def should_skip_url(self, url: str) -> bool:
        """Check if URL should be skipped entirely."""
        decoded_url = unquote(url)

        for pattern in self._skip_patterns:
            if pattern.search(decoded_url):
                return True

        return False

    def is_document_url(self, url: str) -> bool:
        """Check if URL points to a downloadable document."""
        parsed = urlparse(url)
        path_lower = unquote(parsed.path.lower())

        for ext in DOCUMENT_EXTENSIONS:
            if path_lower.endswith(ext):
                return True

        # Check query parameters (some sites use download.php?file=xxx.pdf)
        query_lower = parsed.query.lower()
        for ext in DOCUMENT_EXTENSIONS_PRIMARY:
            if ext in query_lower:
                return True

        return False

    def is_archive_url(self, url: str) -> bool:
        """Check if URL points to an archive file."""
        parsed = urlparse(url)
        path_lower = unquote(parsed.path.lower())

        for ext in DOCUMENT_EXTENSIONS_ARCHIVES:
            if path_lower.endswith(ext):
                return True

        return False

    def is_admission_url(self, url: str) -> bool:
        """Check if URL pattern suggests admission content."""
        decoded_url = unquote(url)

        # Strong patterns (highest priority)
        for pattern in self._url_patterns_strong:
            if pattern.search(decoded_url):
                return True

        # Moderate patterns
        for pattern in self._url_patterns_moderate:
            if pattern.search(decoded_url):
                return True

        # Check for keywords in URL
        if self._keyword_pattern.search(decoded_url):
            return True

        return False

    def is_admission_filename(self, filename: str) -> bool:
        """Check if filename suggests admission document."""
        decoded = unquote(filename)

        for pattern in self._filename_patterns:
            if pattern.search(decoded):
                return True

        # Check for admission keywords in filename
        if self._core_keyword_pattern.search(decoded):
            return True

        return False

    def score_content(self, text: str, title: Optional[str] = None) -> float:
        """Score content for admission relevance (0-1)."""
        if not text:
            return 0.0

        total_words = len(text.split())
        if total_words == 0:
            return 0.0

        score = 0.0

        # Core keyword matches (highest value)
        core_matches = self._core_keyword_pattern.findall(text)
        if core_matches:
            score += min(len(core_matches) * 0.15, 0.5)

        # General keyword matches
        all_matches = self._keyword_pattern.findall(text)
        keyword_count = len(all_matches)

        # Keyword density score
        density = keyword_count / total_words
        score += min(density * 30, 0.3)

        # Bonus for title match
        if title:
            title_core = self._core_keyword_pattern.findall(title)
            title_all = self._keyword_pattern.findall(title)
            if title_core:
                score += 0.3
            elif title_all:
                score += 0.15

        # Bonus for multiple distinct keywords
        unique_keywords = len(set(m.lower() for m in all_matches))
        if unique_keywords >= 10:
            score += 0.2
        elif unique_keywords >= 5:
            score += 0.1
        elif unique_keywords >= 3:
            score += 0.05

        return min(score, 1.0)

    def is_admission_related(
        self,
        url: str,
        text: Optional[str] = None,
        title: Optional[str] = None
    ) -> tuple[bool, float]:
        """Check if content is admission related and return score."""
        score = 0.0

        # URL-based detection with priority
        decoded_url = unquote(url)

        # Strong URL patterns
        for pattern in self._url_patterns_strong:
            if pattern.search(decoded_url):
                score = max(score, 0.7)
                break

        # Moderate URL patterns
        if score < 0.7:
            for pattern in self._url_patterns_moderate:
                if pattern.search(decoded_url):
                    score = max(score, 0.5)
                    break

        # URL keyword check
        if self._core_keyword_pattern.search(decoded_url):
            score = max(score, 0.6)
        elif self._keyword_pattern.search(decoded_url):
            score = max(score, 0.4)

        # Document/filename check
        if self.is_document_url(url):
            parsed = urlparse(url)
            filename = unquote(parsed.path.split("/")[-1])
            if self.is_admission_filename(filename):
                score = max(score, 0.8)

        # Content-based detection
        if text:
            content_score = self.score_content(text, title)
            # Combine URL and content scores
            score = max(score, content_score)
            # Bonus if both URL and content are relevant
            if score >= 0.3 and content_score >= 0.3:
                score = min(score + 0.1, 1.0)

        return score >= self.min_score, score

    def prioritize_url(self, url: str) -> int:
        """Return priority score for URL (higher = more priority).

        Priority levels:
        - 100+: Strong admission URLs
        - 80-99: Admission documents
        - 60-79: Moderate admission URLs
        - 40-59: Documents with admission keywords
        - 20-39: URLs with admission keywords
        - 0: No admission indicators
        """
        decoded_url = unquote(url)

        # Strong URL patterns (highest priority)
        for pattern in self._url_patterns_strong:
            if pattern.search(decoded_url):
                return 120

        # Document with admission filename
        if self.is_document_url(decoded_url):
            parsed = urlparse(decoded_url)
            filename = unquote(parsed.path.split("/")[-1])

            if self.is_admission_filename(filename):
                return 100

            # Core keywords in filename
            if self._core_keyword_pattern.search(filename):
                return 90

            # Any keywords in filename
            if self._keyword_pattern.search(filename):
                return 70

            # Document but no keywords
            return 40

        # Moderate URL patterns
        for pattern in self._url_patterns_moderate:
            if pattern.search(decoded_url):
                return 80

        # Core keywords in URL
        if self._core_keyword_pattern.search(decoded_url):
            return 85

        # Any keywords in URL
        matches = self._keyword_pattern.findall(decoded_url)
        if matches:
            return 50 + min(len(matches) * 5, 30)

        return 0

    def get_document_priority(self, url: str, filename: str) -> int:
        """Get priority specifically for document downloads."""
        priority = 0

        # Check filename for admission patterns
        if self.is_admission_filename(filename):
            priority += 100

        # Core keywords in filename
        core_matches = self._core_keyword_pattern.findall(filename)
        priority += len(core_matches) * 20

        # General keywords in filename
        all_matches = self._keyword_pattern.findall(filename)
        priority += len(all_matches) * 5

        # URL patterns
        decoded_url = unquote(url)
        for pattern in self._url_patterns_strong:
            if pattern.search(decoded_url):
                priority += 50
                break

        # File type priority
        filename_lower = filename.lower()
        if filename_lower.endswith('.pdf'):
            priority += 10
        elif filename_lower.endswith(('.hwp', '.hwpx')):
            priority += 8
        elif filename_lower.endswith(('.doc', '.docx')):
            priority += 5

        return priority
