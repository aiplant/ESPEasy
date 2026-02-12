"""
Data models for executive-business school matching.
数据模型：企业、高管、商学院、映射关系
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExecutiveRole(Enum):
    """高管职位类型"""
    CEO = "CEO"
    HR_VP = "人力副总"
    CFO = "财务总监"
    CTO = "CTO"
    COO = "COO"
    BOARD_MEMBER = "董事"
    OTHER = "其他高管"


class BusinessSchoolName(Enum):
    """目标商学院"""
    CEIBS = "中欧国际工商学院"
    CKGSB = "长江商学院"
    PBC = "五道口金融学院"
    GUANGHUA = "北大光华管理学院"
    TSINGHUA_SEM = "清华经管学院"
    FUDAN_MGMT = "复旦管理学院"
    SJTU_ACEM = "上交安泰经管学院"
    OTHER = "其他商学院"


class DataSource(Enum):
    """数据来源"""
    QICHACHA = "企查查"
    TIANYANCHA = "天眼查"
    LINKEDIN = "领英"
    PUBLIC_NEWS = "公开新闻"
    ALUMNI_DIRECTORY = "校友录"


@dataclass
class Company:
    """企业信息"""
    name: str
    unified_social_credit_code: str = ""  # 统一社会信用代码
    registration_number: str = ""
    industry: str = ""
    province: str = ""
    city: str = ""
    bid_project_name: str = ""  # 中标/招标项目名
    bid_type: str = ""  # 中标 or 招标
    bid_amount: float = 0.0
    bid_date: str = ""

    @property
    def id_key(self) -> str:
        return self.unified_social_credit_code or self.name


@dataclass
class Executive:
    """高管信息"""
    name: str
    role: ExecutiveRole
    company_name: str
    company_credit_code: str = ""
    gender: str = ""
    phone: str = ""
    email: str = ""
    linkedin_url: str = ""
    data_source: DataSource = DataSource.QICHACHA

    @property
    def id_key(self) -> str:
        return f"{self.company_name}:{self.name}:{self.role.value}"


@dataclass
class BusinessSchoolRecord:
    """商学院就读记录"""
    school: BusinessSchoolName
    school_name_raw: str = ""  # 原始名称（用于非枚举匹配）
    program: str = ""  # EMBA / MBA / 总裁班 etc.
    class_year: str = ""  # 届别，如 "2018级"
    graduation_year: str = ""
    data_source: DataSource = DataSource.ALUMNI_DIRECTORY
    confidence: float = 0.0  # 匹配置信度 0.0 ~ 1.0
    evidence_url: str = ""  # 证据链接


@dataclass
class ExecutiveSchoolMapping:
    """核心映射关系：企业-高管-商学院-届别"""
    company: Company
    executive: Executive
    school_records: list[BusinessSchoolRecord] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.school_records:
            return f"{self.company.name} | {self.executive.name}({self.executive.role.value}) | 未匹配商学院"
        parts = []
        for rec in self.school_records:
            school_label = rec.school_name_raw or rec.school.value
            parts.append(f"{school_label} {rec.program} {rec.class_year}")
        return (
            f"{self.company.name} | "
            f"{self.executive.name}({self.executive.role.value}) | "
            f"{'; '.join(parts)}"
        )

    def to_dict(self) -> dict:
        return {
            "company_name": self.company.name,
            "company_credit_code": self.company.unified_social_credit_code,
            "company_industry": self.company.industry,
            "company_province": self.company.province,
            "bid_project": self.company.bid_project_name,
            "bid_type": self.company.bid_type,
            "bid_amount": self.company.bid_amount,
            "executive_name": self.executive.name,
            "executive_role": self.executive.role.value,
            "school_records": [
                {
                    "school": r.school.value,
                    "school_raw": r.school_name_raw,
                    "program": r.program,
                    "class_year": r.class_year,
                    "graduation_year": r.graduation_year,
                    "confidence": r.confidence,
                    "data_source": r.data_source.value,
                    "evidence_url": r.evidence_url,
                }
                for r in self.school_records
            ],
        }
