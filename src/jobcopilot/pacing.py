"""节奏控制与熔断。

目的：让自动化行为接近一个正常人一天的投递量，降低对平台的冲击。
不是绕过检测——这两件事必须分清。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime


@dataclass
class PacingConfig:
    batch_size: int = 8
    interval_seconds: float = 6.0
    interval_jitter: float = 2.0
    batch_pause_seconds: float = 90.0
    platform_daily_cap: int = 50
    total_daily_cap: int = 80
    session_max_minutes: int = 45

    @classmethod
    def from_dict(cls, raw: dict) -> "PacingConfig":
        allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (raw or {}).items() if k in allowed})


@dataclass
class BreakerConfig:
    fail_streak_limit: int = 3
    stop_on_needs_human: bool = True
    stop_on_degraded_health: bool = True
    human_takeover_timeout: int = 600

    @classmethod
    def from_dict(cls, raw: dict) -> "BreakerConfig":
        allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (raw or {}).items() if k in allowed})


class CircuitBreaker(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Pacer:
    """计时、批次休息、日上限与熔断判断。dry_run 时压缩等待时间。"""

    def __init__(self, pacing: PacingConfig, breaker: BreakerConfig, dry_run: bool = False):
        self.p = pacing
        self.b = breaker
        self.dry_run = dry_run
        self.started_at = datetime.now()
        self.applied_in_batch = 0
        self.total_applied = 0
        self.fail_streak = 0

    # ---- 计时 ----

    def _sleep(self, seconds: float) -> None:
        if self.dry_run:
            time.sleep(min(seconds, 0.05))
            return
        time.sleep(seconds)

    def wait_between_applies(self) -> None:
        base = self.p.interval_seconds
        jitter = self.p.interval_jitter
        delay = max(0.0, base + random.uniform(-jitter, jitter))
        self._sleep(delay)

    def count_applied(self) -> None:
        """记录一次成功投递，并在批满时休息。"""
        self.applied_in_batch += 1
        self.total_applied += 1
        if self.applied_in_batch >= self.p.batch_size:
            self.applied_in_batch = 0
            self._sleep(self.p.batch_pause_seconds)

    # ---- 上限 ----

    def check_limits(self, platform_today: int) -> None:
        if platform_today >= self.p.platform_daily_cap:
            raise CircuitBreaker(f"达到单平台日上限 {self.p.platform_daily_cap}")
        if self.total_applied >= self.p.total_daily_cap:
            raise CircuitBreaker(f"达到全平台日上限 {self.p.total_daily_cap}")
        elapsed_min = (datetime.now() - self.started_at).total_seconds() / 60
        if elapsed_min >= self.p.session_max_minutes:
            raise CircuitBreaker(f"达到单次运行时长上限 {self.p.session_max_minutes} 分钟")

    # ---- 熔断 ----

    def record_result(self, status: str) -> None:
        if status == "failed":
            self.fail_streak += 1
            if self.fail_streak >= self.b.fail_streak_limit:
                raise CircuitBreaker(f"连续失败达到 {self.b.fail_streak_limit} 次")
        elif status == "success":
            self.fail_streak = 0
        elif status == "needs_human" and self.b.stop_on_needs_human:
            raise CircuitBreaker("出现需要人工接管的场景，立即停机")

    def check_health(self, health_status: str) -> None:
        if health_status == "degraded" and self.b.stop_on_degraded_health:
            raise CircuitBreaker("页面结构告警（关键选择器失效），停机待修")
        if health_status == "logged_out":
            raise CircuitBreaker("登录态失效，需人工重新登录")

    # ---- 观测 ----

    def snapshot(self) -> dict:
        return {
            "applied_in_batch": self.applied_in_batch,
            "total_applied": self.total_applied,
            "fail_streak": self.fail_streak,
            "elapsed_seconds": int((datetime.now() - self.started_at).total_seconds()),
        }
