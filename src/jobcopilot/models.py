"""数据模型与状态枚举。所有平台适配器都往这里对齐。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ApplyStatus(str, Enum):
    SUCCESS = "success"
    DUPLICATE = "duplicate"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"
    PENDING = "pending"
    UNKNOWN = "unknown"


TERMINAL = {
    ApplyStatus.SUCCESS,
    ApplyStatus.DUPLICATE,
    ApplyStatus.BLOCKED,
    ApplyStatus.SKIPPED,
    ApplyStatus.FAILED,
    ApplyStatus.NEEDS_HUMAN,
}


@dataclass
class Job:
    platform: str
    job_id: str
    company: str
    title: str
    city: str
    salary_raw: str = ""
    url: str = ""
    posted_at: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    jd_hash: str = ""


@dataclass
class ApplyResult:
    status: ApplyStatus
    reason: str = ""
    evidence: Optional[str] = None
    elapsed_ms: int = 0


@dataclass
class Message:
    platform: str
    message_id: str
    kind: str  # reply / invite / system / other
    preview: str = ""
    received_at: Optional[str] = None
    needs_reply: bool = False


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"  # 页面结构变化或关键选择器失效
    LOGGED_OUT = "logged_out"
    PAUSED = "paused"


@dataclass
class Health:
    status: HealthStatus
    detail: str = ""


@dataclass
class Query:
    keywords: str
    city: str = ""
    salary_min: Optional[int] = None
    pages: int = 1
    extra: dict = field(default_factory=dict)


@dataclass
class RunContext:
    config: dict
    dry_run: bool = False
    repo_root: str = ""
