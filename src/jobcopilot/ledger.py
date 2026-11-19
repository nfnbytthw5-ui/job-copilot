"""台账：SQLite 存储、三源去重、幂等写入、崩溃恢复。

设计要点见 docs/ledger-spec.md：
  - 投递前写 pending，执行后写入终态（两次写，不是一次）
  - UNIQUE(platform, job_id) 兜底
  - pending 超时按是否有证据判定 success / unknown，unknown 不重投
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import ApplyStatus, Job
from .normalize import normalize_city, normalize_company, normalize_title

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    company         TEXT NOT NULL,
    company_norm    TEXT NOT NULL,
    title           TEXT NOT NULL,
    title_norm      TEXT NOT NULL,
    city            TEXT NOT NULL,
    city_norm       TEXT NOT NULL,
    salary_raw      TEXT,
    url             TEXT,
    jd_hash         TEXT,
    status          TEXT NOT NULL,
    reason          TEXT,
    evidence        TEXT,
    resume_version  TEXT,
    account         TEXT,
    batch_id        TEXT,
    attempted_at    TEXT NOT NULL,
    settled_at      TEXT,
    last_seen_at    TEXT,
    UNIQUE(platform, job_id)
);
CREATE INDEX IF NOT EXISTS idx_ctc ON applications(company_norm, title_norm, city_norm);
CREATE INDEX IF NOT EXISTS idx_attempted ON applications(attempted_at);
"""


class DuplicateJob(Exception):
    """同一 (platform, job_id) 已存在。"""


class Ledger:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- 查询 ----------

    def exists(self, platform: str, job_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM applications WHERE platform=? AND job_id=?",
            (platform, job_id),
        ).fetchone()
        return row is not None

    def cross_platform_dup(self, job: Job, window_days: int = 180) -> Optional[sqlite3.Row]:
        """跨平台近似：公司 + 岗位 + 城市 三中，且时间窗内。

        只比较其他平台。同平台的重复由 job_id 判定，不该走到这里。
        """
        since = (datetime.now() - timedelta(days=window_days)).isoformat()
        return self.conn.execute(
            """SELECT * FROM applications
               WHERE platform != ? AND company_norm=? AND title_norm=? AND city_norm=?
                 AND attempted_at >= ?
               ORDER BY attempted_at DESC LIMIT 1""",
            (
                job.platform,
                normalize_company(job.company),
                normalize_title(job.title),
                normalize_city(job.city),
                since,
            ),
        ).fetchone()

    def in_batch(self, batch_id: str, job: Job) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM applications WHERE batch_id=?
               AND (job_id=? OR (company_norm=? AND title_norm=? AND city_norm=?))
               LIMIT 1""",
            (
                batch_id,
                job.job_id,
                normalize_company(job.company),
                normalize_title(job.title),
                normalize_city(job.city),
            ),
        ).fetchone()

    # ---------- 写入 ----------

    def new_batch_id(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def begin(self, job: Job, batch_id: str, resume_version: str = "", account: str = "") -> None:
        """投递前写 pending。重复插入抛 DuplicateJob。"""
        now = datetime.now().isoformat(timespec="seconds")
        try:
            self.conn.execute(
                """INSERT INTO applications
                   (platform, job_id, company, company_norm, title, title_norm,
                    city, city_norm, salary_raw, url, jd_hash, status,
                    resume_version, account, batch_id, attempted_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.platform, job.job_id, job.company, normalize_company(job.company),
                    job.title, normalize_title(job.title),
                    job.city, normalize_city(job.city),
                    job.salary_raw, job.url, job.jd_hash, ApplyStatus.PENDING.value,
                    resume_version, account, batch_id, now, now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateJob(f"{job.platform}/{job.job_id} 已存在于台账") from exc

    def settle(self, job: Job, status: ApplyStatus | str, reason: str = "",
               evidence: Optional[str] = None) -> None:
        status = status.value if isinstance(status, ApplyStatus) else status
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            """UPDATE applications SET status=?, reason=?, evidence=?, settled_at=?, last_seen_at=?
               WHERE platform=? AND job_id=?""",
            (status, reason, evidence, now, now, job.platform, job.job_id),
        )
        self.conn.commit()

    def touch_seen(self, platform: str, job_id: str) -> None:
        self.conn.execute(
            "UPDATE applications SET last_seen_at=? WHERE platform=? AND job_id=?",
            (datetime.now().isoformat(timespec="seconds"), platform, job_id),
        )
        self.conn.commit()

    # ---------- 崩溃恢复 ----------

    def recover_pending(self, stale_minutes: int = 30) -> dict:
        """把超时 pending 判定为 success / unknown。返回统计。"""
        cutoff = (datetime.now() - timedelta(minutes=stale_minutes)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM applications WHERE status='pending' AND attempted_at <= ?",
            (cutoff,),
        ).fetchall()

        stats = {"success": 0, "unknown": 0}
        for row in rows:
            status = ApplyStatus.SUCCESS if row["evidence"] else ApplyStatus.UNKNOWN
            reason = "崩溃恢复：有证据" if row["evidence"] else "崩溃恢复：无证据，需人工核验，不重投"
            self.conn.execute(
                "UPDATE applications SET status=?, reason=?, settled_at=? WHERE id=?",
                (status.value, reason, datetime.now().isoformat(timespec="seconds"), row["id"]),
            )
            stats[status.value] += 1
        self.conn.commit()
        return stats

    # ---------- 统计 ----------

    def today_counts(self, day: str | None = None) -> dict:
        day = day or datetime.now().strftime("%Y-%m-%d")
        rows = self.conn.execute(
            """SELECT status, COUNT(*) AS n FROM applications
               WHERE attempted_at >= ? AND attempted_at < ? || '~'
               GROUP BY status""",
            (day, day),
        ).fetchall()
        counts = {s.value: 0 for s in ApplyStatus}
        for row in rows:
            counts[row["status"]] = row["n"]
        return counts

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM applications ORDER BY attempted_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
