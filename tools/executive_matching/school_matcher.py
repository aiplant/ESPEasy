"""
Business school matching engine.
商学院匹配引擎：基于多源数据匹配高管与商学院的关系
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

from .config import MatchingConfig
from .models import (
    BusinessSchoolName,
    BusinessSchoolRecord,
    DataSource,
    Executive,
)

logger = logging.getLogger(__name__)

# Canonical mapping from school enum to its standard name
SCHOOL_ENUM_MAP: dict[str, BusinessSchoolName] = {
    "中欧国际工商学院": BusinessSchoolName.CEIBS,
    "长江商学院": BusinessSchoolName.CKGSB,
    "五道口金融学院": BusinessSchoolName.PBC,
    "北大光华管理学院": BusinessSchoolName.GUANGHUA,
    "清华经管学院": BusinessSchoolName.TSINGHUA_SEM,
    "复旦管理学院": BusinessSchoolName.FUDAN_MGMT,
    "上交安泰经管学院": BusinessSchoolName.SJTU_ACEM,
}


def _normalize(text: str) -> str:
    """Normalize whitespace and case for matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[len(b)]


class SchoolMatchSource(ABC):
    """Abstract source for business school alumni matching."""

    @abstractmethod
    def find_school_records(
        self,
        executive: Executive,
        config: MatchingConfig,
    ) -> list[BusinessSchoolRecord]:
        """Search this source for school records matching the executive."""


class TextMatcher:
    """
    Matches school/program/class-year from free-form text.
    Used by multiple sources (news, LinkedIn profiles, alumni directories).
    """

    def __init__(self, config: MatchingConfig):
        self.config = config
        self._school_patterns = self._build_school_patterns()
        self._program_patterns = self._build_program_patterns()
        self._class_year_regexes = [
            re.compile(p) for p in config.class_year_patterns
        ]

    def _build_school_patterns(self) -> list[tuple[str, list[re.Pattern]]]:
        """Pre-compile regex patterns for each school."""
        result = []
        for school_name, keywords in self.config.school_keywords.items():
            patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
            result.append((school_name, patterns))
        return result

    def _build_program_patterns(self) -> list[tuple[str, list[re.Pattern]]]:
        result = []
        for program_name, keywords in self.config.program_keywords.items():
            patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
            result.append((program_name, patterns))
        return result

    def extract_school_mentions(self, text: str) -> list[dict]:
        """
        Extract all school mentions from text.
        Returns list of dicts with keys: school_name, program, class_year, span.
        """
        results = []
        for school_name, patterns in self._school_patterns:
            for pat in patterns:
                for match in pat.finditer(text):
                    # Look for program and class year in surrounding context
                    start = max(0, match.start() - 50)
                    end = min(len(text), match.end() + 80)
                    context = text[start:end]

                    program = self._extract_program(context)
                    class_year = self._extract_class_year(context)

                    results.append({
                        "school_name": school_name,
                        "program": program,
                        "class_year": class_year,
                        "span": (match.start(), match.end()),
                    })
        # Deduplicate by school name
        seen = set()
        deduped = []
        for r in results:
            key = (r["school_name"], r["program"], r["class_year"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped

    def _extract_program(self, context: str) -> str:
        for program_name, patterns in self._program_patterns:
            for pat in patterns:
                if pat.search(context):
                    return program_name
        return ""

    def _extract_class_year(self, context: str) -> str:
        for regex in self._class_year_regexes:
            m = regex.search(context)
            if m:
                return m.group(0)
        return ""


class NewsMatchSource(SchoolMatchSource):
    """
    Match executives against public news articles.
    In production, this would call a news search API.
    """

    def __init__(self, config: MatchingConfig, news_search_fn=None):
        self.config = config
        self.text_matcher = TextMatcher(config)
        self._search_fn = news_search_fn

    def find_school_records(
        self,
        executive: Executive,
        config: MatchingConfig,
    ) -> list[BusinessSchoolRecord]:
        if not self._search_fn:
            logger.debug("No news search function configured; skipping")
            return []

        query = f"{executive.name} {executive.company_name} 商学院"
        articles = self._search_fn(query)
        records = []
        for article in articles:
            text = article.get("title", "") + " " + article.get("content", "")
            url = article.get("url", "")
            mentions = self.text_matcher.extract_school_mentions(text)
            # Verify executive name appears in same article
            if executive.name not in text:
                continue
            for mention in mentions:
                school_enum = SCHOOL_ENUM_MAP.get(
                    mention["school_name"], BusinessSchoolName.OTHER
                )
                records.append(BusinessSchoolRecord(
                    school=school_enum,
                    school_name_raw=mention["school_name"],
                    program=mention["program"],
                    class_year=mention["class_year"],
                    data_source=DataSource.PUBLIC_NEWS,
                    confidence=0.7,
                    evidence_url=url,
                ))
        return records


class LinkedInMatchSource(SchoolMatchSource):
    """
    Match executives against LinkedIn profile data.
    In production, this would call LinkedIn API or a scraper service.
    """

    def __init__(self, config: MatchingConfig, linkedin_fetch_fn=None):
        self.config = config
        self.text_matcher = TextMatcher(config)
        self._fetch_fn = linkedin_fetch_fn

    def find_school_records(
        self,
        executive: Executive,
        config: MatchingConfig,
    ) -> list[BusinessSchoolRecord]:
        if not self._fetch_fn:
            logger.debug("No LinkedIn fetch function configured; skipping")
            return []

        profile = self._fetch_fn(executive.name, executive.company_name)
        if not profile:
            return []

        records = []
        # Check education section
        educations = profile.get("education", [])
        for edu in educations:
            school_text = edu.get("school", "")
            degree_text = edu.get("degree", "")
            year_text = edu.get("year", "")
            full_text = f"{school_text} {degree_text} {year_text}"

            mentions = self.text_matcher.extract_school_mentions(full_text)
            for mention in mentions:
                school_enum = SCHOOL_ENUM_MAP.get(
                    mention["school_name"], BusinessSchoolName.OTHER
                )
                records.append(BusinessSchoolRecord(
                    school=school_enum,
                    school_name_raw=mention["school_name"],
                    program=mention["program"] or degree_text,
                    class_year=mention["class_year"] or year_text,
                    data_source=DataSource.LINKEDIN,
                    confidence=0.9,
                    evidence_url=executive.linkedin_url,
                ))
        return records


class AlumniDirectoryMatchSource(SchoolMatchSource):
    """
    Match executives against alumni directory data.
    Expects pre-loaded alumni data as list of dicts:
      [{"name": "...", "school": "...", "program": "...", "class_year": "..."}, ...]
    """

    def __init__(
        self,
        config: MatchingConfig,
        alumni_data: Optional[list[dict]] = None,
    ):
        self.config = config
        self.alumni_data = alumni_data or []
        # Build name index for fast lookup
        self._name_index: dict[str, list[dict]] = {}
        for record in self.alumni_data:
            name = record.get("name", "").strip()
            if name:
                self._name_index.setdefault(name, []).append(record)

    def find_school_records(
        self,
        executive: Executive,
        config: MatchingConfig,
    ) -> list[BusinessSchoolRecord]:
        records = []

        # Exact match
        matches = self._name_index.get(executive.name, [])

        # Fuzzy match if no exact match
        if not matches and config.name_fuzzy_max_distance > 0:
            matches = self._fuzzy_lookup(
                executive.name, config.name_fuzzy_max_distance
            )

        for alumni in matches:
            school_name = alumni.get("school", "")
            school_enum = SCHOOL_ENUM_MAP.get(school_name, BusinessSchoolName.OTHER)

            # Boost confidence if company also matches
            confidence = 0.85
            if alumni.get("company") and alumni["company"] == executive.company_name:
                confidence = 0.95

            records.append(BusinessSchoolRecord(
                school=school_enum,
                school_name_raw=school_name,
                program=alumni.get("program", ""),
                class_year=alumni.get("class_year", ""),
                data_source=DataSource.ALUMNI_DIRECTORY,
                confidence=confidence,
            ))
        return records

    def _fuzzy_lookup(self, name: str, max_distance: int) -> list[dict]:
        results = []
        for indexed_name, records in self._name_index.items():
            if _edit_distance(name, indexed_name) <= max_distance:
                results.extend(records)
        return results


class SchoolMatchingEngine:
    """
    Orchestrates matching across multiple sources and deduplicates results.
    核心匹配引擎：聚合多源匹配结果
    """

    def __init__(self, config: MatchingConfig):
        self.config = config
        self.sources: list[SchoolMatchSource] = []

    def add_source(self, source: SchoolMatchSource) -> None:
        self.sources.append(source)

    def match(self, executive: Executive) -> list[BusinessSchoolRecord]:
        """
        Run the executive through all sources, deduplicate, and filter
        by confidence threshold.
        """
        all_records: list[BusinessSchoolRecord] = []
        for source in self.sources:
            try:
                found = source.find_school_records(executive, self.config)
                all_records.extend(found)
            except Exception:
                logger.exception(
                    "Source %s failed for %s",
                    source.__class__.__name__,
                    executive.name,
                )

        # Deduplicate: same school + program → keep highest confidence
        deduped = self._deduplicate(all_records)

        # Filter by confidence threshold
        return [
            r for r in deduped
            if r.confidence >= self.config.min_confidence_threshold
        ]

    def _deduplicate(
        self, records: list[BusinessSchoolRecord]
    ) -> list[BusinessSchoolRecord]:
        best: dict[str, BusinessSchoolRecord] = {}
        for r in records:
            key = f"{r.school.value}:{r.program}"
            if key not in best or r.confidence > best[key].confidence:
                best[key] = r
        return list(best.values())
