"""编排主循环：搜索 → 三源去重 → 投递 → 落盘 → 战报。"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path

from .adapter import AdapterLoadError, load_adapter
from .ledger import DuplicateJob, Ledger
from .models import ApplyStatus, Job, Query, RunContext
from .normalize import normalize_city, normalize_company, normalize_title
from .pacing import BreakerConfig, CircuitBreaker, Pacer, PacingConfig


class Blacklist:
    """闸1：公司 / 行业 / 岗位关键词。命中即跳过，不消耗页面请求。"""

    def __init__(self, raw: dict | None):
        raw = raw or {}
        self.companies = [normalize_company(c) for c in raw.get("companies", []) if c]
        self.industries = [str(i).lower() for i in raw.get("industries", []) if i]
        self.title_keywords = [str(k).lower() for k in raw.get("title_keywords", []) if k]

    def match(self, job: Job) -> tuple[bool, str]:
        company_norm = normalize_company(job.company)
        for blocked in self.companies:
            if blocked and (company_norm == blocked or company_norm.startswith(blocked)):
                return True, f"公司黑名单：{blocked}"

        for tag in job.tags:
            tag_lower = str(tag).lower()
            for industry in self.industries:
                if industry and industry in tag_lower:
                    return True, f"行业黑名单：{industry}"

        title_lower = job.title.lower()
        for keyword in self.title_keywords:
            if keyword and keyword in title_lower:
                return True, f"岗位关键词黑名单：{keyword}"

        return False, ""


class Runner:
    def __init__(self, config: dict, repo_root: Path, dry_run: bool = False):
        self.config = config
        self.repo_root = repo_root
        self.dry_run = dry_run
        self.blacklist = Blacklist(config.get("blacklist"))
        self.blocked = {"company": 0, "industry": 0, "title": 0}
        self.batch_stats = {
            ApplyStatus.SUCCESS.value: 0,
            ApplyStatus.DUPLICATE.value: 0,
            ApplyStatus.BLOCKED.value: 0,
            ApplyStatus.SKIPPED.value: 0,
            ApplyStatus.FAILED.value: 0,
            ApplyStatus.NEEDS_HUMAN.value: 0,
        }
        self.seen_in_batch: set[tuple] = set()
        self.platform_reports: list[dict] = []
        self.breaker_reason: str | None = None

        paths = config.get("paths", {})
        self.ledger = Ledger(repo_root / paths.get("ledger_db", "data/ledger.db"))
        self.reports_dir = repo_root / paths.get("reports_dir", "reports")
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 主流程 ----------------

    def run(self) -> dict:
        started = datetime.now()
        recovery = self.ledger.recover_pending(
            self.config.get("recovery", {}).get("stale_minutes", 30)
        )

        batch_id = self.ledger.new_batch_id()
        resume_version = self.config.get("resume", {}).get("version", "")

        for platform_name, spec in self.config.get("platforms", {}).items():
            if not spec.get("enabled", True):
                continue
            self._run_platform(platform_name, spec, batch_id, resume_version)

        summary = self._build_summary(started, batch_id, recovery)
        self._write_report(summary)
        self.ledger.close()
        return summary

    def _run_platform(self, name: str, spec: dict, batch_id: str, resume_version: str) -> None:
        pacing = PacingConfig.from_dict({**self.config.get("pacing", {}), **spec.get("pacing", {})})
        breaker = BreakerConfig.from_dict(self.config.get("circuit_breaker", {}))
        pacer = Pacer(pacing, breaker, dry_run=self.dry_run)

        report = {"platform": name, "status": "ok", "reason": "", "counts": {}, "messages": 0}
        today_before = self._platform_today_count(name)

        try:
            adapter = load_adapter(name, spec, self.repo_root)
            ctx = RunContext(config=self.config, dry_run=self.dry_run, repo_root=str(self.repo_root))
            adapter.open(ctx)

            health = adapter.health()
            pacer.check_health(health.status.value)

            query = Query(**{
                k: v for k, v in spec.get("query", {}).items()
                if k in {"keywords", "city", "salary_min", "pages", "extra"}
            })
            jobs = list(adapter.search(query))
            report["found"] = len(jobs)

            for job in jobs:
                job.platform = name
                pacer.check_limits(today_before + report["counts"].get("success", 0))
                self._handle_job(job, adapter, pacer, batch_id, resume_version, report)
                if report["counts"].get("success", 0) and report["counts"]["success"] % pacing.batch_size == 0:
                    pass  # 批间休息由 pacer.count_applied 处理

            adapter.close()

        except CircuitBreaker as exc:
            report["status"] = "stopped"
            report["reason"] = exc.reason
            self.breaker_reason = exc.reason
        except AdapterLoadError as exc:
            report["status"] = "error"
            report["reason"] = str(exc)
        except Exception as exc:  # 适配器内部异常不应拖垮其他平台
            report["status"] = "error"
            report["reason"] = f"{type(exc).__name__}: {exc}"
            if self.dry_run:
                report["traceback"] = traceback.format_exc(limit=3)

        self.platform_reports.append(report)

    def _handle_job(self, job: Job, adapter, pacer: Pacer, batch_id: str,
                    resume_version: str, report: dict) -> None:
        counts = report.setdefault("counts", {})

        def bump(status: ApplyStatus) -> None:
            counts[status.value] = counts.get(status.value, 0) + 1
            if status.value in self.batch_stats:
                self.batch_stats[status.value] += 1

        # 闸1：黑名单
        blocked, reason = self.blacklist.match(job)
        if blocked:
            self._record_blocked(job, batch_id, resume_version, reason)
            bump(ApplyStatus.BLOCKED)
            report["blocked_detail"] = report.get("blocked_detail", [])
            report["blocked_detail"].append(f"{job.company}｜{job.title}｜{reason}")
            return

        # 闸3：批内（先于历史台账，因为批内判断更便宜）
        batch_key = (job.platform, job.job_id)
        cross_key = (normalize_company(job.company), normalize_title(job.title), normalize_city(job.city))
        if batch_key in self.seen_in_batch or cross_key in self.seen_in_batch:
            bump(ApplyStatus.DUPLICATE)
            report["dup_detail"] = report.get("dup_detail", 0) + 1
            return
        self.seen_in_batch.add(batch_key)
        self.seen_in_batch.add(cross_key)

        # 闸2：历史台账
        if self.ledger.exists(job.platform, job.job_id):
            self.ledger.touch_seen(job.platform, job.job_id)
            bump(ApplyStatus.DUPLICATE)
            report["dup_ledger"] = report.get("dup_ledger", 0) + 1
            return

        cross_dup = self.ledger.cross_platform_dup(job)
        if cross_dup is not None:
            self._record_blocked(job, batch_id, resume_version,
                                 f"跨平台重复（{cross_dup['platform']}/{cross_dup['job_id']}）")
            bump(ApplyStatus.DUPLICATE)
            report["dup_cross"] = report.get("dup_cross", 0) + 1
            return

        # 投递：先写 pending，再执行，最后落终态
        try:
            self.ledger.begin(job, batch_id, resume_version=resume_version,
                             account=self.config.get("account", {}).get("fingerprint", ""))
        except DuplicateJob:
            bump(ApplyStatus.DUPLICATE)
            return

        result = adapter.apply(job)
        status = result.status if isinstance(result.status, ApplyStatus) else ApplyStatus(result.status)
        self.ledger.settle(job, status, result.reason, result.evidence)
        bump(status)

        if status == ApplyStatus.SUCCESS:
            pacer.count_applied()
        pacer.record_result(status.value)

        if status in (ApplyStatus.SUCCESS, ApplyStatus.DUPLICATE):
            pacer.wait_between_applies()

    def _record_blocked(self, job: Job, batch_id: str, resume_version: str, reason: str) -> None:
        """黑名单/跨平台重复写一条跳过记录，不算投递。"""
        if self.ledger.exists(job.platform, job.job_id):
            return
        try:
            self.ledger.begin(job, batch_id, resume_version=resume_version)
            self.ledger.settle(job, ApplyStatus.BLOCKED, reason)
        except DuplicateJob:
            pass

    def _platform_today_count(self, platform: str) -> int:
        day = datetime.now().strftime("%Y-%m-%d")
        rows = self.ledger.conn.execute(
            """SELECT COUNT(*) AS n FROM applications
               WHERE platform=? AND status='success' AND attempted_at >= ?""",
            (platform, day),
        ).fetchone()
        return int(rows["n"] or 0)

    # ---------------- 战报 ----------------

    def _build_summary(self, started: datetime, batch_id: str, recovery: dict) -> dict:
        today = started.strftime("%Y-%m-%d")
        counts = self.ledger.today_counts(today)
        target = self.config.get("pacing", {}).get("total_daily_cap", 80)
        actual = counts.get(ApplyStatus.SUCCESS.value, 0)

        deviations = []
        if actual < target:
            if recovery.get("unknown"):
                deviations.append("崩溃恢复存在 unknown，需人工核验")
            if self.breaker_reason:
                deviations.append(f"熔断停机：{self.breaker_reason}")
            if not deviations:
                deviations.append("候选池不足或未跑满")

        return {
            "date": today,
            "batch_id": batch_id,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": self.dry_run,
            "target": target,
            "actual": actual,
            "deviation": target - actual,
            "deviation_reason": "；".join(deviations) if deviations else "",
            "counts": counts,
            "run_counts": self.batch_stats,
            "recovery": recovery,
            "breaker": self.breaker_reason,
            "platforms": self.platform_reports,
        }

    def _write_report(self, summary: dict) -> None:
        md_path = self.reports_dir / f"{summary['date']}.md"
        json_path = self.reports_dir / f"{summary['date']}.json"

        lines = [
            f"# 求职战报 {summary['date']}",
            "",
            f"- 批次：{summary['batch_id']}",
            f"- 模式：{'dry-run（未真实投递）' if summary['dry_run'] else '实投'}",
            f"- 时效：{summary['started_at']} → {summary['finished_at']}",
            "",
            "## 1. 投递数：目标 vs 实际",
            "",
            f"- 目标 {summary['target']} / 实际 {summary['actual']} / 偏差 {summary['deviation']}",
            f"- 偏差原因：{summary['deviation_reason'] or '无'}",
            "",
            "## 2. 重复拦截",
            "",
            f"- 历史台账命中：{summary['run_counts'].get('duplicate', 0)}",
            "",
            "## 3. 站内消息",
            "",
            "- 本次未拉取消息（dry-run 不含消息处理）",
            "",
            "## 4. 邮件收发",
            "",
            "- 不在本工具范围，由独立邮件模块统计",
            "",
            "## 5. 黑名单生效",
            "",
            f"- 拦截 {summary['run_counts'].get('blocked', 0)} 条，抽样见平台明细",
            "",
            "## 6. 次日计划",
            "",
            "- 待填：时段安排 / 候选池余量 / 待修项",
            "",
            "## 平台明细",
            "",
        ]
        for p in summary["platforms"]:
            lines.append(f"### {p['platform']}（{p['status']}）")
            lines.append(f"- 取岗 {p.get('found', 0)} / 计数 {p.get('counts', {})}")
            if p.get("reason"):
                lines.append(f"- 说明：{p['reason']}")
            lines.append("")

        md_path.write_text("\n".join(lines), encoding="utf-8")
        json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
