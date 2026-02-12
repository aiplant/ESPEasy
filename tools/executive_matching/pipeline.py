"""
Data pipeline orchestrator.
数据管道：串联企业数据提取 → 高管筛选 → 商学院匹配 → 输出
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from .config import PipelineConfig
from .enterprise_api import EnterpriseAPIClient, create_client
from .export import CSVExporter, ExcelExporter, JSONExporter, ReportExporter
from .models import (
    BusinessSchoolRecord,
    Company,
    DataSource,
    Executive,
    ExecutiveSchoolMapping,
)
from .school_matcher import (
    AlumniDirectoryMatchSource,
    LinkedInMatchSource,
    NewsMatchSource,
    SchoolMatchingEngine,
)

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Main pipeline: Company list → Executive extraction → School matching → Export.

    Usage:
        config = PipelineConfig.from_env()
        pipeline = Pipeline(config)
        pipeline.load_companies_from_bid_list("bids.json")
        results = pipeline.run()
        pipeline.export(results, format="excel")
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.companies: list[Company] = []
        self._api_client: Optional[EnterpriseAPIClient] = None
        self._matching_engine: Optional[SchoolMatchingEngine] = None

    def _get_api_client(self) -> EnterpriseAPIClient:
        if self._api_client is None:
            # Prefer Tianyancha if token is set, else Qichacha
            if self.config.api.tianyancha_api_token:
                source = DataSource.TIANYANCHA
            else:
                source = DataSource.QICHACHA
            cache_dir = self.config.cache_dir if self.config.enable_cache else None
            self._api_client = create_client(source, self.config.api, cache_dir)
        return self._api_client

    def _get_matching_engine(self) -> SchoolMatchingEngine:
        if self._matching_engine is None:
            engine = SchoolMatchingEngine(self.config.matching)
            # Register default sources
            engine.add_source(NewsMatchSource(self.config.matching))
            engine.add_source(LinkedInMatchSource(self.config.matching))
            engine.add_source(AlumniDirectoryMatchSource(self.config.matching))
            self._matching_engine = engine
        return self._matching_engine

    def set_api_client(self, client: EnterpriseAPIClient) -> None:
        """Inject a custom API client (useful for testing)."""
        self._api_client = client

    def set_matching_engine(self, engine: SchoolMatchingEngine) -> None:
        """Inject a custom matching engine (useful for testing)."""
        self._matching_engine = engine

    # ── Data Loading ──────────────────────────────────────────────

    def load_companies_from_bid_list(self, path: str) -> int:
        """
        Load companies from a JSON bid/tender list file.
        Expected format:
        [
          {
            "company_name": "...",
            "project_name": "...",
            "bid_type": "中标",
            "amount": 1000000,
            "date": "2024-01-15"
          },
          ...
        ]
        Returns number of companies loaded.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for item in data:
            company = Company(
                name=item["company_name"],
                bid_project_name=item.get("project_name", ""),
                bid_type=item.get("bid_type", ""),
                bid_amount=item.get("amount", 0),
                bid_date=item.get("date", ""),
            )
            self.companies.append(company)

        logger.info("Loaded %d companies from %s", len(data), path)
        return len(data)

    def load_companies_from_names(self, names: list[str]) -> int:
        """Load companies from a simple list of names."""
        for name in names:
            self.companies.append(Company(name=name))
        return len(names)

    def load_alumni_data(self, path: str) -> None:
        """
        Load alumni directory data and register it as a matching source.
        Expected JSON format:
        [
          {"name": "张三", "school": "中欧国际工商学院", "program": "EMBA",
           "class_year": "2018级", "company": "某某公司"},
          ...
        ]
        """
        with open(path, "r", encoding="utf-8") as f:
            alumni_data = json.load(f)
        engine = self._get_matching_engine()
        source = AlumniDirectoryMatchSource(self.config.matching, alumni_data)
        engine.add_source(source)
        logger.info("Loaded %d alumni records from %s", len(alumni_data), path)

    # ── Pipeline Execution ────────────────────────────────────────

    def run(self) -> list[ExecutiveSchoolMapping]:
        """
        Execute the full pipeline:
        1. Enrich company data via API
        2. Extract executives per company
        3. Match each executive against business school sources
        4. Build and return the mapping results
        """
        if not self.companies:
            logger.warning("No companies to process")
            return []

        client = self._get_api_client()
        engine = self._get_matching_engine()
        results: list[ExecutiveSchoolMapping] = []

        total = len(self.companies)
        for idx, company in enumerate(self.companies, 1):
            logger.info(
                "[%d/%d] Processing: %s", idx, total, company.name
            )

            # Step 1: Enrich company info
            enriched = client.search_company(company.name)
            if enriched:
                # Preserve bid info from original record
                enriched.bid_project_name = company.bid_project_name
                enriched.bid_type = company.bid_type
                enriched.bid_amount = company.bid_amount
                enriched.bid_date = company.bid_date
                company = enriched

            # Step 2: Get filtered executives
            executives = client.get_filtered_executives(
                company, self.config.target_roles
            )
            if not executives:
                logger.info("  No target executives found for %s", company.name)
                continue

            logger.info("  Found %d target executives", len(executives))

            # Step 3: Match each executive concurrently
            mappings = self._match_executives(company, executives, engine)
            results.extend(mappings)

        matched = sum(1 for r in results if r.school_records)
        logger.info(
            "Pipeline complete: %d companies, %d executives, %d with school matches",
            total,
            len(results),
            matched,
        )
        return results

    def _match_executives(
        self,
        company: Company,
        executives: list[Executive],
        engine: SchoolMatchingEngine,
    ) -> list[ExecutiveSchoolMapping]:
        """Match a batch of executives using thread pool."""
        mappings: list[ExecutiveSchoolMapping] = []

        def _match_one(exec_: Executive) -> ExecutiveSchoolMapping:
            records = engine.match(exec_)
            return ExecutiveSchoolMapping(
                company=company,
                executive=exec_,
                school_records=records,
            )

        if self.config.max_workers > 1 and len(executives) > 1:
            with ThreadPoolExecutor(
                max_workers=self.config.max_workers
            ) as pool:
                futures = {
                    pool.submit(_match_one, e): e for e in executives
                }
                for future in as_completed(futures):
                    try:
                        mappings.append(future.result())
                    except Exception:
                        exec_ = futures[future]
                        logger.exception(
                            "  Failed to match: %s", exec_.name
                        )
        else:
            for exec_ in executives:
                try:
                    mappings.append(_match_one(exec_))
                except Exception:
                    logger.exception("  Failed to match: %s", exec_.name)

        return mappings

    # ── Export ─────────────────────────────────────────────────────

    def export(
        self,
        results: list[ExecutiveSchoolMapping],
        fmt: str = "excel",
        filename: Optional[str] = None,
    ) -> str:
        """
        Export results to file. Returns the output file path.
        Supported formats: json, csv, excel
        """
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        exporters: dict[str, type[ReportExporter]] = {
            "json": JSONExporter,
            "csv": CSVExporter,
            "excel": ExcelExporter,
        }
        exporter_cls = exporters.get(fmt)
        if not exporter_cls:
            raise ValueError(
                f"Unsupported format: {fmt}. Use one of: {list(exporters.keys())}"
            )

        exporter = exporter_cls()
        default_name = f"executive_school_mapping.{exporter.file_extension}"
        output_path = str(output_dir / (filename or default_name))
        exporter.export(results, output_path)

        logger.info("Exported %d records to %s", len(results), output_path)
        return output_path
