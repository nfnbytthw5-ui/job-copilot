"""核心逻辑测试：归一化、幂等、崩溃恢复。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobcopilot.ledger import DuplicateJob, Ledger  # noqa: E402
from jobcopilot.models import ApplyStatus, Job  # noqa: E402
from jobcopilot.normalize import (  # noqa: E402
    normalize_city,
    normalize_company,
    normalize_title,
)


def make_job(job_id: str = "j-1") -> Job:
    return Job(
        platform="test",
        job_id=job_id,
        company="沃尔沃汽车台州工厂",
        title="质量经理(台州)",
        city="台州市",
        salary_raw="35-55K",
    )


class NormalizeTest(unittest.TestCase):
    def test_company_strips_legal_suffix_and_parentheses(self):
        self.assertEqual(
            normalize_company("迈得医疗工业设备股份有限公司（Maider Medical，688310.SH）"),
            "迈得医疗工业设备",
        )

    def test_company_keeps_branch_identity(self):
        self.assertEqual(
            normalize_company("北京汽车股份有限公司株洲分公司"),
            "北京汽车株洲分公司",
        )
        self.assertNotEqual(
            normalize_company("北京汽车股份有限公司"),
            normalize_company("北京汽车股份有限公司株洲分公司"),
        )

    def test_title_keeps_level(self):
        self.assertNotEqual(normalize_title("质量经理"), normalize_title("质量管理/测试工程师"))
        self.assertEqual(normalize_title("高级质量经理(台州)"), normalize_title("质量经理"))

    def test_city_collapses_to_city_level(self):
        self.assertEqual(normalize_city("台州市"), "台州")
        self.assertEqual(normalize_city("台州"), "台州")


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.db")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_duplicate_insert_rejected(self):
        job = make_job()
        self.ledger.begin(job, batch_id="b1")
        with self.assertRaises(DuplicateJob):
            self.ledger.begin(job, batch_id="b2")

    def test_same_job_other_platform_allowed(self):
        job = make_job()
        self.ledger.begin(job, batch_id="b1")
        job2 = make_job()
        job2.platform = "other"
        self.ledger.begin(job2, batch_id="b1")  # 不应抛异常

    def test_recovery_with_evidence_is_success(self):
        job = make_job("j-evidence")
        self.ledger.begin(job, batch_id="b1")
        self.ledger.conn.execute(
            "UPDATE applications SET attempted_at='2026-01-01T00:00:00', evidence='x.png' WHERE job_id=?",
            (job.job_id,),
        )
        self.ledger.conn.commit()
        stats = self.ledger.recover_pending(stale_minutes=30)
        self.assertEqual(stats["success"], 1)
        row = self.ledger.conn.execute(
            "SELECT status FROM applications WHERE job_id=?", (job.job_id,)
        ).fetchone()
        self.assertEqual(row["status"], ApplyStatus.SUCCESS.value)

    def test_recovery_without_evidence_is_unknown(self):
        job = make_job("j-no-evidence")
        self.ledger.begin(job, batch_id="b1")
        self.ledger.conn.execute(
            "UPDATE applications SET attempted_at='2026-01-01T00:00:00' WHERE job_id=?",
            (job.job_id,),
        )
        self.ledger.conn.commit()
        stats = self.ledger.recover_pending(stale_minutes=30)
        self.assertEqual(stats["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
