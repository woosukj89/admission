"""Fetch and manage Korean university URLs"""

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from ..models import University
from ..utils.logging import get_logger

logger = get_logger("universities.fetcher")


# Curated list of major Korean universities with verified admission URLs
MAJOR_UNIVERSITIES = [
    # SKY (Top 3)
    University(name="서울대학교", name_en="Seoul National University", url="https://www.snu.ac.kr", admission_url="https://admission.snu.ac.kr", location="서울", university_type="국립"),
    University(name="고려대학교", name_en="Korea University", url="https://www.korea.ac.kr", admission_url="https://oku.korea.ac.kr", location="서울", university_type="사립"),
    University(name="연세대학교", name_en="Yonsei University", url="https://www.yonsei.ac.kr", admission_url="https://admission.yonsei.ac.kr", location="서울", university_type="사립"),

    # Major Seoul Universities
    University(name="성균관대학교", name_en="Sungkyunkwan University", url="https://www.skku.edu", admission_url="https://admission.skku.edu", location="서울", university_type="사립"),
    University(name="한양대학교", name_en="Hanyang University", url="https://www.hanyang.ac.kr", admission_url="https://go.hanyang.ac.kr", location="서울", university_type="사립"),
    University(name="서강대학교", name_en="Sogang University", url="https://www.sogang.ac.kr", admission_url="https://admission.sogang.ac.kr", location="서울", university_type="사립"),
    University(name="중앙대학교", name_en="Chung-Ang University", url="https://www.cau.ac.kr", admission_url="https://admission.cau.ac.kr", location="서울", university_type="사립"),
    University(name="경희대학교", name_en="Kyung Hee University", url="https://www.khu.ac.kr", admission_url="https://iphak.khu.ac.kr", location="서울", university_type="사립"),
    University(name="이화여자대학교", name_en="Ewha Womans University", url="https://www.ewha.ac.kr", admission_url="https://admission.ewha.ac.kr", location="서울", university_type="사립"),
    University(name="홍익대학교", name_en="Hongik University", url="https://www.hongik.ac.kr", admission_url="https://admission.hongik.ac.kr", location="서울", university_type="사립"),
    University(name="건국대학교", name_en="Konkuk University", url="https://www.konkuk.ac.kr", admission_url="https://enter.konkuk.ac.kr", location="서울", university_type="사립"),
    University(name="동국대학교", name_en="Dongguk University", url="https://www.dongguk.edu", admission_url="https://ipsi.dongguk.edu", location="서울", university_type="사립"),
    University(name="국민대학교", name_en="Kookmin University", url="https://www.kookmin.ac.kr", admission_url="https://admission.kookmin.ac.kr", location="서울", university_type="사립"),
    University(name="숭실대학교", name_en="Soongsil University", url="https://www.ssu.ac.kr", admission_url="https://admission.ssu.ac.kr", location="서울", university_type="사립"),
    University(name="세종대학교", name_en="Sejong University", url="https://www.sejong.ac.kr", admission_url="https://ipsi.sejong.ac.kr", location="서울", university_type="사립"),
    University(name="광운대학교", name_en="Kwangwoon University", url="https://www.kw.ac.kr", admission_url="https://ipsi.kw.ac.kr", location="서울", university_type="사립"),
    University(name="명지대학교", name_en="Myongji University", url="https://www.mju.ac.kr", admission_url="https://iphak.mju.ac.kr", location="서울", university_type="사립"),
    University(name="상명대학교", name_en="Sangmyung University", url="https://www.smu.ac.kr", admission_url="https://admission.smu.ac.kr", location="서울", university_type="사립"),
    University(name="서울시립대학교", name_en="University of Seoul", url="https://www.uos.ac.kr", admission_url="https://ipsi.uos.ac.kr", location="서울", university_type="공립"),
    University(name="서울과학기술대학교", name_en="Seoul National University of Science and Technology", url="https://www.seoultech.ac.kr", admission_url="https://ipsi.seoultech.ac.kr", location="서울", university_type="국립"),

    # Science/Technology Universities
    University(name="KAIST", name_en="Korea Advanced Institute of Science and Technology", url="https://www.kaist.ac.kr", admission_url="https://admission.kaist.ac.kr", location="대전", university_type="국립"),
    University(name="POSTECH", name_en="Pohang University of Science and Technology", url="https://www.postech.ac.kr", admission_url="https://adm-iu.postech.ac.kr", location="포항", university_type="사립"),
    University(name="UNIST", name_en="Ulsan National Institute of Science and Technology", url="https://www.unist.ac.kr", admission_url="https://www.unist.ac.kr/admission", location="울산", university_type="국립"),
    University(name="GIST", name_en="Gwangju Institute of Science and Technology", url="https://www.gist.ac.kr", admission_url="https://admission.gist.ac.kr", location="광주", university_type="국립"),
    University(name="DGIST", name_en="Daegu Gyeongbuk Institute of Science and Technology", url="https://www.dgist.ac.kr", admission_url="https://admission.dgist.ac.kr", location="대구", university_type="국립"),

    # Major Regional National Universities
    University(name="부산대학교", name_en="Pusan National University", url="https://www.pusan.ac.kr", admission_url="https://go.pusan.ac.kr", location="부산", university_type="국립"),
    University(name="경북대학교", name_en="Kyungpook National University", url="https://www.knu.ac.kr", admission_url="https://enter.knu.ac.kr", location="대구", university_type="국립"),
    University(name="전남대학교", name_en="Chonnam National University", url="https://www.jnu.ac.kr", admission_url="https://admission.jnu.ac.kr", location="광주", university_type="국립"),
    University(name="충남대학교", name_en="Chungnam National University", url="https://www.cnu.ac.kr", admission_url="https://ipsi.cnu.ac.kr", location="대전", university_type="국립"),
    University(name="충북대학교", name_en="Chungbuk National University", url="https://www.chungbuk.ac.kr", admission_url="https://ipsi.chungbuk.ac.kr", location="청주", university_type="국립"),
    University(name="전북대학교", name_en="Jeonbuk National University", url="https://www.jbnu.ac.kr", admission_url="https://ipsi.jbnu.ac.kr", location="전주", university_type="국립"),
    University(name="강원대학교", name_en="Kangwon National University", url="https://www.kangwon.ac.kr", admission_url="https://enter.kangwon.ac.kr", location="춘천", university_type="국립"),
    University(name="제주대학교", name_en="Jeju National University", url="https://www.jejunu.ac.kr", admission_url="https://ipsi.jejunu.ac.kr", location="제주", university_type="국립"),
    University(name="인하대학교", name_en="Inha University", url="https://www.inha.ac.kr", admission_url="https://admission.inha.ac.kr", location="인천", university_type="사립"),
    University(name="아주대학교", name_en="Ajou University", url="https://www.ajou.ac.kr", admission_url="https://ipsi.ajou.ac.kr", location="수원", university_type="사립"),

    # Other Major Universities
    University(name="단국대학교", name_en="Dankook University", url="https://www.dankook.ac.kr", admission_url="https://ipsi.dankook.ac.kr", location="용인", university_type="사립"),
    University(name="숙명여자대학교", name_en="Sookmyung Women's University", url="https://www.sookmyung.ac.kr", admission_url="https://admission.sookmyung.ac.kr", location="서울", university_type="사립"),
    University(name="한국외국어대학교", name_en="Hankuk University of Foreign Studies", url="https://www.hufs.ac.kr", admission_url="https://ipsi.hufs.ac.kr", location="서울", university_type="사립"),
    University(name="가톨릭대학교", name_en="Catholic University of Korea", url="https://www.catholic.ac.kr", admission_url="https://ipsi.catholic.ac.kr", location="서울", university_type="사립"),
    University(name="한국항공대학교", name_en="Korea Aerospace University", url="https://www.kau.ac.kr", admission_url="https://ipsi.kau.ac.kr", location="고양", university_type="사립"),
    University(name="서울여자대학교", name_en="Seoul Women's University", url="https://www.swu.ac.kr", admission_url="https://ipsi.swu.ac.kr", location="서울", university_type="사립"),
    University(name="덕성여자대학교", name_en="Duksung Women's University", url="https://www.duksung.ac.kr", admission_url="https://ipsi.duksung.ac.kr", location="서울", university_type="사립"),
    University(name="동덕여자대학교", name_en="Dongduk Women's University", url="https://www.dongduk.ac.kr", admission_url="https://ipsi.dongduk.ac.kr", location="서울", university_type="사립"),
    University(name="성신여자대학교", name_en="Sungshin Women's University", url="https://www.sungshin.ac.kr", admission_url="https://ipsi.sungshin.ac.kr", location="서울", university_type="사립"),

    # Regional Private Universities
    University(name="영남대학교", name_en="Yeungnam University", url="https://www.yu.ac.kr", admission_url="https://admission.yu.ac.kr", location="경산", university_type="사립"),
    University(name="계명대학교", name_en="Keimyung University", url="https://www.kmu.ac.kr", admission_url="https://admission.kmu.ac.kr", location="대구", university_type="사립"),
    University(name="동아대학교", name_en="Dong-A University", url="https://www.donga.ac.kr", admission_url="https://ipsi.donga.ac.kr", location="부산", university_type="사립"),
    University(name="부경대학교", name_en="Pukyong National University", url="https://www.pknu.ac.kr", admission_url="https://ipsi.pknu.ac.kr", location="부산", university_type="국립"),
    University(name="울산대학교", name_en="University of Ulsan", url="https://www.ulsan.ac.kr", admission_url="https://ipsi.ulsan.ac.kr", location="울산", university_type="사립"),
]


class UniversityFetcher:
    """Fetch and manage Korean university list"""

    def __init__(self, cache_path: Optional[Path] = None):
        self.cache_path = cache_path or settings.universities_cache
        self.universities: list[University] = []

    async def fetch_from_wikipedia(self) -> list[University]:
        """Fetch university list from Wikipedia"""
        logger.info("Fetching universities from Wikipedia...")
        universities = []

        # Wikipedia API for list of universities in South Korea
        wiki_url = "https://en.wikipedia.org/wiki/List_of_universities_and_colleges_in_South_Korea"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(wiki_url)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "lxml")

                # Find tables with university data
                tables = soup.find_all("table", class_="wikitable")

                for table in tables:
                    rows = table.find_all("tr")[1:]  # Skip header
                    for row in rows:
                        cells = row.find_all(["td", "th"])
                        if len(cells) >= 2:
                            # Try to extract name and URL
                            name_cell = cells[0]
                            link = name_cell.find("a")

                            if link and link.get("href"):
                                name = link.get_text(strip=True)
                                # Skip if it's just a reference number
                                if name and not name.startswith("["):
                                    # Try to find university website from Wikipedia article
                                    wiki_page_url = urljoin("https://en.wikipedia.org", link["href"])
                                    website = await self._get_university_website(client, wiki_page_url)

                                    if website:
                                        universities.append(University(
                                            name=name,
                                            name_en=name,
                                            url=website,
                                        ))

        except Exception as e:
            logger.warning(f"Failed to fetch from Wikipedia: {e}")

        logger.info(f"Found {len(universities)} universities from Wikipedia")
        return universities

    async def _get_university_website(self, client: httpx.AsyncClient, wiki_url: str) -> Optional[str]:
        """Extract official website from a Wikipedia article"""
        try:
            response = await client.get(wiki_url, follow_redirects=True)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "lxml")
            infobox = soup.find("table", class_="infobox")

            if infobox:
                # Look for website row
                for row in infobox.find_all("tr"):
                    header = row.find("th")
                    if header and "website" in header.get_text(strip=True).lower():
                        link = row.find("a", class_="external")
                        if link and link.get("href"):
                            return link["href"]

            # Try external links section
            external_links = soup.find("span", id="External_links")
            if external_links:
                parent = external_links.find_parent()
                if parent:
                    next_ul = parent.find_next_sibling("ul")
                    if next_ul:
                        first_link = next_ul.find("a", class_="external")
                        if first_link and first_link.get("href"):
                            return first_link["href"]

        except Exception:
            pass

        return None

    def load_cached(self) -> list[University]:
        """Load universities from cache file"""
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.universities = [University(**u) for u in data]
                    logger.info(f"Loaded {len(self.universities)} universities from cache")
                    return self.universities
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")

        return []

    def save_cache(self) -> None:
        """Save universities to cache file"""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            data = [u.model_dump() for u in self.universities]
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(self.universities)} universities to cache")

    async def update(self, use_fallback: bool = True) -> list[University]:
        """Update university list from all sources"""
        # Start with curated list
        self.universities = list(MAJOR_UNIVERSITIES)
        seen_urls = {u.url for u in self.universities}

        # Try to fetch from Wikipedia
        try:
            wiki_universities = await self.fetch_from_wikipedia()
            for uni in wiki_universities:
                if uni.url not in seen_urls:
                    self.universities.append(uni)
                    seen_urls.add(uni.url)
        except Exception as e:
            logger.warning(f"Wikipedia fetch failed: {e}")

        # Save to cache
        self.save_cache()

        logger.info(f"Total universities: {len(self.universities)}")
        return self.universities

    def get_universities(self) -> list[University]:
        """Get list of universities, loading from cache if needed"""
        if not self.universities:
            self.universities = self.load_cached()

        if not self.universities:
            # Use fallback curated list
            self.universities = list(MAJOR_UNIVERSITIES)
            self.save_cache()

        return self.universities

    def find_by_name(self, name: str) -> Optional[University]:
        """Find a university by name (Korean or English)"""
        name_lower = name.lower()
        for uni in self.get_universities():
            if name_lower in uni.name.lower():
                return uni
            if uni.name_en and name_lower in uni.name_en.lower():
                return uni
        return None
