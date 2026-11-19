"""离线演示适配器：不访问任何网站，用于跑通流水线。"""

from __future__ import annotations

import hashlib
import random

from jobcopilot.models import (
    ApplyResult,
    ApplyStatus,
    Health,
    HealthStatus,
    Job,
    Query,
)

_COMPANIES = [
    ("某某制药有限公司", ["制药", "1000-4999人"]),
    ("某某人力资源服务有限公司", ["人力资源服务", "100-499人"]),
    ("台州某汽车零部件有限公司", ["汽车零部件", "500-999人"]),
    ("浙江某新能源科技有限公司", ["新能源", "1000-4999人"]),
    ("某精密机械股份有限公司", ["机械制造", "500-999人"]),
    ("某外资汽车系统有限公司", ["汽车零部件", "1000-4999人"]),
    ("某汽车电子有限公司", ["汽车电子", "1000-4999人"]),
    ("某动力科技有限公司", ["动力总成", "500-999人"]),
    ("某智能装备有限公司", ["智能制造", "100-499人"]),
    ("某新材料科技有限公司", ["新材料", "500-999人"]),
    ("某工业自动化有限公司", ["工业自动化", "1000-4999人"]),
    ("某汽车零部件集团有限公司", ["汽车零部件", "1000-4999人"]),
]

_TITLES = [
    "质量总监", "质量经理", "质量管理/测试工程师", "体系工程师",
    "SQE 供应商质量工程师", "质量体系审核员", "检验员", "质量检验员",
]


class Adapter:
    display_name = "离线演示"

    def __init__(self, job_count: int = 60, seed: int = 42):
        self.job_count = job_count
        self.seed = seed
        self._applied: set[str] = set()

    def open(self, ctx) -> None:
        return None

    def health(self) -> Health:
        return Health(HealthStatus.OK, "离线演示，无需登录")

    def search(self, query: Query) -> list[Job]:
        rng = random.Random(self.seed)
        jobs: list[Job] = []
        for i in range(self.job_count):
            company, tags = _COMPANIES[i % len(_COMPANIES)]
            # 公司轮次 × 标题轮次，保证组合基本唯一
            title = _TITLES[(i // len(_COMPANIES)) % len(_TITLES)]
            job_id = f"mock-{i:04d}"

            # 每 12 个重复前一个，模拟翻页重叠
            if i > 0 and i % 12 == 0:
                job_id = f"mock-{i - 1:04d}"
                i_repeat = i - 1
                company, tags = _COMPANIES[i_repeat % len(_COMPANIES)]
                title = _TITLES[i_repeat % len(_TITLES)]

            jd = f"{company} {title} JD 样本 {job_id}"
            jobs.append(
                Job(
                    platform="mock",
                    job_id=job_id,
                    company=company,
                    title=title,
                    city=query.city or "台州",
                    salary_raw=f"{rng.choice([20, 25, 30, 35, 40, 45])}-"
                               f"{rng.choice([30, 35, 40, 50, 55, 60])}K",
                    url=f"https://example.com/jobs/{job_id}",
                    tags=list(tags),
                    jd_hash=hashlib.sha1(jd.encode()).hexdigest()[:16],
                )
            )
        return jobs

    def apply(self, job: Job) -> ApplyResult:
        if job.job_id in self._applied:
            return ApplyResult(ApplyStatus.DUPLICATE, "演示：同一岗位重复投递")
        self._applied.add(job.job_id)

        # 偶发跳过，模拟"已下线"
        if job.job_id.endswith("6"):
            return ApplyResult(ApplyStatus.SKIPPED, "演示：岗位已下线")

        return ApplyResult(
            ApplyStatus.SUCCESS,
            "演示：投递成功",
            evidence=f"data/evidence/{job.platform}/{job.job_id}.png",
        )

    def inbox(self, since: str | None = None) -> list:
        return []

    def close(self) -> None:
        return None
