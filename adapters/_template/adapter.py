"""平台适配器模板。

复制整个目录为 adapters/<platform>/，然后：
  1. 在 SELECTORS 里填页面选择器
  2. 实现下面 6 个方法
  3. 在 config.yaml 的 platforms 下注册 module 路径

硬性要求见 docs/adapter-contract.md，最容易被忽略的三条：
  - search() 必须无副作用，不得触发投递或打招呼
  - apply() 必须幂等，同一 job_id 第二次返回 DUPLICATE
  - 不得在此处登录、输密码、过验证码，遇到即返回 NEEDS_HUMAN
"""

from __future__ import annotations

from jobcopilot.models import (
    ApplyResult,
    ApplyStatus,
    Health,
    HealthStatus,
    Job,
    Message,
    Query,
)

# 页面改版只需改这一处
SELECTORS = {
    "job_card": ".job-card",
    "job_title": ".job-card .title",
    "job_company": ".job-card .company",
    "job_salary": ".job-card .salary",
    "job_link": ".job-card a.detail",
    "apply_button": ".job-detail .btn-apply",
    "applied_flag": ".job-detail .status-applied",
    "login_flag": ".header .user-avatar",
    "captcha": ".captcha-container",
}

# 改版时最先失效的几个，health() 只检查这些
CRITICAL_SELECTORS = ["job_card", "job_title", "job_link", "login_flag"]


class Adapter:
    display_name = "示例平台"

    def __init__(self, session_url: str = "", **_):
        # 只接收非敏感配置。不要把密码、Cookie 放这里。
        self.session_url = session_url
        self._applied: set[str] = set()

    # ---------- 会话 ----------

    def open(self, ctx) -> None:
        """接管一个已登录的浏览器会话。此处不做登录。"""
        raise NotImplementedError("请接入你的浏览器驱动（如 Playwright）")

    def health(self) -> Health:
        """检查登录态 + 关键选择器是否还在。"""
        raise NotImplementedError

    # ---------- 取岗（只读） ----------

    def search(self, query: Query) -> list[Job]:
        """翻页读取岗位卡片，解析成 Job。

        job_id 必须稳定：优先用 URL 里的岗位 ID，不要用列表下标。
        """
        raise NotImplementedError

    # ---------- 投递（唯一副作用） ----------

    def apply(self, job: Job) -> ApplyResult:
        """执行一次投递。

        1. 先读页面状态，已投递 → DUPLICATE
        2. 点投递
        3. 等页面出现明确成功态，留存证据
        4. 无成功态 → FAILED；出现验证码 → NEEDS_HUMAN
        """
        if job.job_id in self._applied:
            return ApplyResult(ApplyStatus.DUPLICATE, "本进程内已投递")
        raise NotImplementedError

    # ---------- 消息（只读） ----------

    def inbox(self, since: str | None = None) -> list[Message]:
        """读站内消息。不得自动回复、不得自动加好友。"""
        return []

    def close(self) -> None:
        """释放会话，保留登录态。不要清 Cookie。"""
        return None
