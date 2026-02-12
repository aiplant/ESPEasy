"""
Enterprise data API clients for Qichacha and Tianyancha.
企业数据接口：从企查查/天眼查提取企业高管信息
"""

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from .config import APIConfig
from .models import Company, DataSource, Executive, ExecutiveRole

logger = logging.getLogger(__name__)

# 职位名称到角色枚举的映射
ROLE_MAPPING: dict[str, ExecutiveRole] = {
    "总经理": ExecutiveRole.CEO,
    "董事长": ExecutiveRole.CEO,
    "首席执行官": ExecutiveRole.CEO,
    "CEO": ExecutiveRole.CEO,
    "法定代表人": ExecutiveRole.CEO,
    "人力资源副总裁": ExecutiveRole.HR_VP,
    "人力资源总监": ExecutiveRole.HR_VP,
    "人力副总": ExecutiveRole.HR_VP,
    "HRD": ExecutiveRole.HR_VP,
    "CHO": ExecutiveRole.HR_VP,
    "CHRO": ExecutiveRole.HR_VP,
    "财务总监": ExecutiveRole.CFO,
    "首席财务官": ExecutiveRole.CFO,
    "CFO": ExecutiveRole.CFO,
    "财务副总裁": ExecutiveRole.CFO,
    "CTO": ExecutiveRole.CTO,
    "首席技术官": ExecutiveRole.CTO,
    "技术副总裁": ExecutiveRole.CTO,
    "COO": ExecutiveRole.COO,
    "首席运营官": ExecutiveRole.COO,
    "运营副总裁": ExecutiveRole.COO,
    "董事": ExecutiveRole.BOARD_MEMBER,
    "独立董事": ExecutiveRole.BOARD_MEMBER,
}


def _classify_role(title: str) -> ExecutiveRole:
    """Map a raw job title string to an ExecutiveRole enum."""
    for keyword, role in ROLE_MAPPING.items():
        if keyword in title:
            return role
    return ExecutiveRole.OTHER


class EnterpriseAPIClient(ABC):
    """Abstract base for enterprise data API clients."""

    def __init__(self, config: APIConfig, cache_dir: Optional[str] = None):
        self.config = config
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        """Enforce QPS rate limiting."""
        if self.config.rate_limit_qps <= 0:
            return
        interval = 1.0 / self.config.rate_limit_qps
        elapsed = time.time() - self._last_request_time
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_time = time.time()

    def _cache_key(self, endpoint: str, params: dict) -> str:
        raw = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[dict]:
        if not self.cache_dir:
            return None
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _set_cached(self, key: str, data: dict) -> None:
        if not self.cache_dir:
            return
        cache_file = self.cache_dir / f"{key}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @abstractmethod
    def search_company(self, company_name: str) -> Optional[Company]:
        """Search for a company by name and return basic info."""

    @abstractmethod
    def get_executives(self, company: Company) -> list[Executive]:
        """Get the list of executives/key personnel for a company."""

    def get_filtered_executives(
        self,
        company: Company,
        target_roles: Optional[list[str]] = None,
    ) -> list[Executive]:
        """Get executives filtered to target roles only."""
        all_execs = self.get_executives(company)
        if not target_roles:
            return all_execs
        target_set = set()
        for role_name in target_roles:
            for keyword, role_enum in ROLE_MAPPING.items():
                if role_name in (keyword, role_enum.value):
                    target_set.add(role_enum)
        return [e for e in all_execs if e.role in target_set]


class QichachaClient(EnterpriseAPIClient):
    """
    企查查 API client.
    Docs: https://openapi.qcc.com/dataApi
    """

    def _request(self, endpoint: str, params: dict) -> Optional[dict]:
        if requests is None:
            logger.error("requests library not installed")
            return None

        cache_key = self._cache_key(f"qcc:{endpoint}", params)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug("Cache hit for %s", endpoint)
            return cached

        self._rate_limit()
        url = f"{self.config.qichacha_base_url}{endpoint}"
        headers = {
            "Key": self.config.qichacha_api_key,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=self.config.request_timeout_sec,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("Status") == "200":
                self._set_cached(cache_key, data)
                return data
            logger.warning("Qichacha API error: %s", data.get("Message"))
            return None
        except Exception:
            logger.exception("Qichacha request failed: %s", endpoint)
            return None

    def search_company(self, company_name: str) -> Optional[Company]:
        data = self._request("/ECISearch/Search", {"keyword": company_name})
        if not data:
            return None
        results = data.get("Result", [])
        if not results:
            return None
        item = results[0]
        return Company(
            name=item.get("Name", company_name),
            unified_social_credit_code=item.get("CreditCode", ""),
            registration_number=item.get("No", ""),
            industry=item.get("Industry", ""),
            province=item.get("Province", ""),
        )

    def get_executives(self, company: Company) -> list[Executive]:
        key = company.unified_social_credit_code or company.name
        data = self._request("/ECISenior/GetList", {"searchKey": key})
        if not data:
            return []
        results = data.get("Result", [])
        executives = []
        for item in results:
            name = item.get("Name", "").strip()
            title = item.get("Job", "").strip()
            if not name:
                continue
            role = _classify_role(title)
            executives.append(Executive(
                name=name,
                role=role,
                company_name=company.name,
                company_credit_code=company.unified_social_credit_code,
                data_source=DataSource.QICHACHA,
            ))
        return executives


class TianyanchaClient(EnterpriseAPIClient):
    """
    天眼查 Open API client.
    Docs: https://open.tianyancha.com/open/818
    """

    def _request(self, endpoint: str, params: dict) -> Optional[dict]:
        if requests is None:
            logger.error("requests library not installed")
            return None

        cache_key = self._cache_key(f"tyc:{endpoint}", params)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug("Cache hit for %s", endpoint)
            return cached

        self._rate_limit()
        url = f"{self.config.tianyancha_base_url}{endpoint}"
        headers = {
            "Authorization": self.config.tianyancha_api_token,
        }
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=self.config.request_timeout_sec,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error_code") == 0:
                self._set_cached(cache_key, data)
                return data
            logger.warning("Tianyancha API error: %s", data.get("reason"))
            return None
        except Exception:
            logger.exception("Tianyancha request failed: %s", endpoint)
            return None

    def search_company(self, company_name: str) -> Optional[Company]:
        data = self._request(
            "/services/open/search/2.0",
            {"word": company_name},
        )
        if not data:
            return None
        items = data.get("result", {}).get("items", [])
        if not items:
            return None
        item = items[0]
        return Company(
            name=item.get("name", company_name),
            unified_social_credit_code=item.get("creditCode", ""),
            registration_number=item.get("regNumber", ""),
            industry=item.get("categoryStr", ""),
            province=item.get("base", ""),
        )

    def get_executives(self, company: Company) -> list[Executive]:
        key = company.unified_social_credit_code or company.name
        data = self._request(
            "/services/open/mr/staff/2.0",
            {"keyword": key},
        )
        if not data:
            return []
        staff_list = data.get("result", {}).get("staffList", [])
        executives = []
        for item in staff_list:
            name = item.get("name", "").strip()
            titles = item.get("typeJoin", [])
            title_str = ",".join(titles) if isinstance(titles, list) else str(titles)
            if not name:
                continue
            role = _classify_role(title_str)
            executives.append(Executive(
                name=name,
                role=role,
                company_name=company.name,
                company_credit_code=company.unified_social_credit_code,
                data_source=DataSource.TIANYANCHA,
            ))
        return executives


def create_client(
    source: DataSource,
    config: APIConfig,
    cache_dir: Optional[str] = None,
) -> EnterpriseAPIClient:
    """Factory: create the appropriate API client."""
    if source == DataSource.QICHACHA:
        return QichachaClient(config, cache_dir)
    elif source == DataSource.TIANYANCHA:
        return TianyanchaClient(config, cache_dir)
    else:
        raise ValueError(f"Unsupported enterprise data source: {source}")
