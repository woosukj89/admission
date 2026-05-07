"""Data models for the crawler"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class CrawlStatus(str, Enum):
    """Status of a crawl task"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ContentType(str, Enum):
    """Types of content that can be crawled"""
    HTML = "html"
    PDF = "pdf"
    DOC = "doc"
    DOCX = "docx"
    HWP = "hwp"
    HWPX = "hwpx"
    XLS = "xls"
    XLSX = "xlsx"
    PPT = "ppt"
    PPTX = "pptx"
    ZIP = "zip"
    RAR = "rar"
    SEVENZ = "7z"
    ALZ = "alz"
    EGG = "egg"
    UNKNOWN = "unknown"


class University(BaseModel):
    """Korean university information"""
    name: str = Field(..., description="University name in Korean")
    name_en: Optional[str] = Field(None, description="University name in English")
    url: str = Field(..., description="Main website URL")
    admission_url: Optional[str] = Field(None, description="Direct admission page URL")
    location: Optional[str] = Field(None, description="City/Province")
    university_type: Optional[str] = Field(None, description="National/Private/etc")

    class Config:
        extra = "allow"


class CrawledPage(BaseModel):
    """A crawled web page"""
    url: str = Field(..., description="Page URL")
    university: str = Field(..., description="University name")
    title: Optional[str] = Field(None, description="Page title")
    content: Optional[str] = Field(None, description="Extracted text content")
    html: Optional[str] = Field(None, description="Raw HTML content")
    links: list[str] = Field(default_factory=list, description="Links found on page")
    content_type: ContentType = Field(default=ContentType.HTML)
    is_admission_related: bool = Field(default=False)
    admission_score: float = Field(default=0.0, description="Relevance score 0-1")
    crawled_at: datetime = Field(default_factory=datetime.now)
    depth: int = Field(default=0, description="Depth from entry point")

    class Config:
        extra = "allow"


class CrawledDocument(BaseModel):
    """A crawled document (PDF, DOC, etc.)"""
    url: str = Field(..., description="Document URL")
    university: str = Field(..., description="University name")
    filename: str = Field(..., description="Original filename")
    file_type: ContentType = Field(...)
    extracted_text: Optional[str] = Field(None, description="Extracted text content")
    file_path: Optional[str] = Field(None, description="Local file path")
    file_size: int = Field(default=0, description="File size in bytes")
    is_admission_related: bool = Field(default=False)
    admission_score: float = Field(default=0.0)
    crawled_at: datetime = Field(default_factory=datetime.now)

    class Config:
        extra = "allow"


class CrawlState(BaseModel):
    """State of a URL in the crawl queue"""
    url: str = Field(..., description="URL to crawl")
    university: str = Field(..., description="University this URL belongs to")
    status: CrawlStatus = Field(default=CrawlStatus.PENDING)
    depth: int = Field(default=0)
    retries: int = Field(default=0)
    error: Optional[str] = Field(None, description="Error message if failed")
    last_attempt: Optional[datetime] = Field(None)

    class Config:
        extra = "allow"


class CrawlStats(BaseModel):
    """Statistics for a crawl session"""
    university: str
    pages_crawled: int = 0
    documents_downloaded: int = 0
    admission_pages_found: int = 0
    errors: int = 0
    start_time: datetime = Field(default_factory=datetime.now)
    end_time: Optional[datetime] = None

    @property
    def duration_seconds(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.now() - self.start_time).total_seconds()
