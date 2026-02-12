"""
Configuration for executive-business school matching.
配置文件：API密钥、匹配参数、目标商学院列表
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class APIConfig:
    """API credentials and endpoints."""
    # 企查查
    qichacha_api_key: str = ""
    qichacha_base_url: str = "https://api.qichacha.com"

    # 天眼查
    tianyancha_api_token: str = ""
    tianyancha_base_url: str = "https://open.api.tianyancha.com"

    # 领英 (via RapidAPI or similar)
    linkedin_api_key: str = ""
    linkedin_base_url: str = ""

    # 请求限流 (requests per second)
    rate_limit_qps: float = 2.0
    request_timeout_sec: int = 30


@dataclass
class MatchingConfig:
    """Matching engine parameters."""
    # 最低置信度阈值，低于此值的匹配结果将被丢弃
    min_confidence_threshold: float = 0.6

    # 姓名模糊匹配容忍度 (编辑距离)
    name_fuzzy_max_distance: int = 1

    # 目标商学院关键词 (用于新闻/领英文本匹配)
    school_keywords: dict[str, list[str]] = field(default_factory=lambda: {
        "中欧国际工商学院": ["中欧", "CEIBS", "中欧国际工商", "中欧商学院"],
        "长江商学院": ["长江", "CKGSB", "长江商学院"],
        "五道口金融学院": ["五道口", "PBC", "五道口金融", "清华五道口"],
        "北大光华管理学院": ["光华", "北大光华", "Guanghua", "光华管理"],
        "清华经管学院": ["清华经管", "清华SEM", "Tsinghua SEM"],
        "复旦管理学院": ["复旦管理", "复旦商学院", "Fudan School of Management"],
        "上交安泰经管学院": ["安泰", "上交安泰", "SJTU ACEM", "交大安泰"],
    })

    # 项目类型关键词
    program_keywords: dict[str, list[str]] = field(default_factory=lambda: {
        "EMBA": ["EMBA", "高级工商管理"],
        "MBA": ["MBA", "工商管理硕士"],
        "总裁班": ["总裁班", "EDP", "高级经理人", "总裁研修"],
        "金融硕士": ["金融硕士", "MF", "Master of Finance"],
        "DBA": ["DBA", "工商管理博士"],
    })

    # 届别提取正则
    class_year_patterns: list[str] = field(default_factory=lambda: [
        r"(\d{4})\s*[级届]",        # 2018级, 2018届
        r"[Cc]lass\s+of\s+(\d{4})",  # Class of 2018
        r"(\d{4})\s*[年].*[入毕]",   # 2018年入学, 2018年毕业
        r"第\s*(\S+)\s*[期届]",       # 第12期
    ])


@dataclass
class PipelineConfig:
    """Overall pipeline configuration."""
    api: APIConfig = field(default_factory=APIConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)

    # 目标高管角色筛选
    target_roles: list[str] = field(default_factory=lambda: [
        "CEO", "人力副总", "财务总监",
    ])

    # 输出目录
    output_dir: str = "output"

    # 并发工作线程数
    max_workers: int = 4

    # 是否启用缓存
    enable_cache: bool = True
    cache_dir: str = ".cache"

    @classmethod
    def from_file(cls, path: str) -> "PipelineConfig":
        """Load configuration from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        config = cls()
        if "api" in data:
            for k, v in data["api"].items():
                if hasattr(config.api, k):
                    setattr(config.api, k, v)
        if "matching" in data:
            for k, v in data["matching"].items():
                if hasattr(config.matching, k):
                    setattr(config.matching, k, v)
        for k in ("target_roles", "output_dir", "max_workers",
                   "enable_cache", "cache_dir"):
            if k in data:
                setattr(config, k, data[k])
        return config

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Load API keys from environment variables."""
        config = cls()
        config.api.qichacha_api_key = os.environ.get("QICHACHA_API_KEY", "")
        config.api.tianyancha_api_token = os.environ.get("TIANYANCHA_API_TOKEN", "")
        config.api.linkedin_api_key = os.environ.get("LINKEDIN_API_KEY", "")
        return config

    def to_file(self, path: str) -> None:
        """Save configuration to a JSON file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "api": {
                "qichacha_base_url": self.api.qichacha_base_url,
                "tianyancha_base_url": self.api.tianyancha_base_url,
                "rate_limit_qps": self.api.rate_limit_qps,
                "request_timeout_sec": self.api.request_timeout_sec,
            },
            "matching": {
                "min_confidence_threshold": self.matching.min_confidence_threshold,
                "name_fuzzy_max_distance": self.matching.name_fuzzy_max_distance,
            },
            "target_roles": self.target_roles,
            "output_dir": self.output_dir,
            "max_workers": self.max_workers,
            "enable_cache": self.enable_cache,
            "cache_dir": self.cache_dir,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
