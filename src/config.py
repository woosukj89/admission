"""Configuration management for the crawler"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class CrawlerSettings(BaseSettings):
    """Crawler configuration settings"""

    # Paths
    data_dir: Path = Field(default=Path("data"), description="Base directory for all data")
    raw_dir: Path = Field(default=Path("data/raw"), description="Raw downloaded files")
    json_dir: Path = Field(default=Path("data/json"), description="Processed JSON files")
    db_path: Path = Field(default=Path("data/admission.db"), description="SQLite database path")
    universities_cache: Path = Field(
        default=Path("src/universities/data/universities.json"),
        description="Cached university list"
    )

    # Crawling behavior
    max_workers: int = Field(default=5, description="Number of concurrent workers")
    max_depth: int = Field(default=3, description="Maximum crawl depth from entry point")
    rate_limit: float = Field(default=1.0, description="Seconds between requests per domain")
    request_timeout: float = Field(default=30.0, description="HTTP request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retries for failed requests")

    # Content limits
    max_page_size: int = Field(default=10 * 1024 * 1024, description="Max page size in bytes (10MB)")
    max_file_size: int = Field(default=50 * 1024 * 1024, description="Max file size in bytes (50MB)")

    # User agent
    user_agent: str = Field(
        default="AdmissionCrawler/0.1 (Educational Research; +https://github.com/admission-crawler)",
        description="User agent string for requests"
    )

    # Caching
    cache_ttl_hours: int = Field(default=24, description="Hours before re-crawling same URL")

    # LLM
    anthropic_api_key: Optional[str] = Field(default=None, description="Anthropic API key for recommendations")
    llm_model: str = Field(default="claude-sonnet-4-20250514", description="LLM model for recommendations")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: Optional[Path] = Field(default=None, description="Log file path")

    model_config = {
        "env_prefix": "CRAWLER_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def ensure_directories(self) -> None:
        """Create all necessary directories"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.universities_cache.parent.mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = CrawlerSettings()
