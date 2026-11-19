"""BOSS 直聘适配器（参考实现）。

状态：未在 CI 环境实测。选择器与流程来自人工跑通的实际页面，平台改版后需校准。
使用前请先 `--dry-run` 跑通 search()，确认取到的岗位与页面一致，再开真实 apply()。

设计约束（见 docs/adapter-contract.md）：
  - 只接管已登录会话，不登录、不碰验证码
  - search() 只读
  - apply() 必须有页面证据才算成功
  - 一切改简历的动作都不在这里做
"""

from __future__ import annotations

import re
import time
from typing import Optional

from jobcopilot.models import (
    ApplyResult,
    ApplyStatus,
    Health,
    HealthStatus,
    Job,
    Message,
    Query,
)

BASE = "https://www.zhipin.com"

SELECTORS = {
    "login_avatar": ".header .user-nav .user-avatar, .header .nav-figure img",
    "job_card": "li.job-card-wrapper, .job-list-box li",
    "job_name": ".job-name, .job-title",
    "job_company": ".company-name",
    "job_salary": ".salary, .job-salary",
    "job_area": ".job-area",
    "detail_link": "a.job-card-left, .job-card-body a",
    "apply_button": ".btn-startchat, .op-btn-chat, .btn-apply",
    "chat_input": ".chat-input, textarea.input-area",
    "captcha": ".verify-slider, .captcha-box, .geetest_panel",
    "resume_entry": ".resume-card, .resume-item",
}

JOB_ID_RE = re.compile(r"/job_detail/([^.]+)\.html")


class Adapter:
    display_name = "BOSS直聘"

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222", **_):
        self.cdp_url = cdp_url
        self._pw = None
        self._browser = None
        self._page = None
        self._applied: set[str] = set()

    # ---------------- 会话 ----------------

    def open(self, ctx) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        contexts = self._browser.contexts
        if not contexts:
            raise RuntimeError("CDP 已连接但没有可用的浏览器上下文，请先手动打开并登录 BOSS")

        # 优先复用已打开 BOSS 页面的上下文，避免新开窗口丢登录态
        self._page = None
        for context in contexts:
            for page in context.pages:
                if "zhipin.com" in (page.url or ""):
                    self._page = page
                    break
            if self._page:
                break

        if self._page is None:
            self._page = contexts[0].new_page()
        self._page.bring_to_front()

    def health(self) -> Health:
        try:
            if self._page.locator(SELECTORS["captcha"]).count() > 0:
                return Health(HealthStatus.PAUSED, "页面出现验证码/滑块，需人工处理")

            if self._page.locator(SELECTORS["login_avatar"]).count() == 0:
                return Health(HealthStatus.LOGGED_OUT, "未检测到登录态，请手动登录")

            # 关键选择器存活检查：列表容器在，说明页面结构没大改
            if self._page.locator(SELECTORS["job_card"]).count() == 0:
                return Health(HealthStatus.DEGRADED, "岗位卡片选择器失效，疑似页面改版")

            return Health(HealthStatus.OK, "")
        except Exception as exc:  # 页面未就绪也算降级，不冒充正常
            return Health(HealthStatus.DEGRADED, f"页面读取失败：{type(exc).__name__}")

    # ---------------- 取岗（只读） ----------------

    def search(self, query: Query) -> list[Job]:
        url = f"{BASE}/web/geek/jobs?query={query.keywords}&city=100000"
        self._page.goto(url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(1500)

        jobs: list[Job] = []
        seen: set[str] = set()

        for page_no in range(max(1, query.pages)):
            cards = self._page.locator(SELECTORS["job_card"])
            for i in range(cards.count()):
                job = self._parse_card(cards.nth(i), query)
                if job and job.job_id not in seen:
                    seen.add(job.job_id)
                    jobs.append(job)

            if page_no + 1 >= query.pages:
                break
            next_btn = self._page.locator("a.ui-icon-arrow-right, .options-pages a:last-child")
            if next_btn.count() == 0:
                break
            next_btn.first.click()
            self._page.wait_for_timeout(1200)

        return jobs

    def _parse_card(self, card, query: Query) -> Optional[Job]:
        try:
            link = card.locator(SELECTORS["detail_link"]).first.get_attribute("href") or ""
            match = JOB_ID_RE.search(link)
            if not match:
                return None

            title = card.locator(SELECTORS['job_name']).first.inner_text()
            company = card.locator(SELECTORS["job_company"]).first.inner_text()
            salary = card.locator(SELECTORS["job_salary"]).first.inner_text()
            area = card.locator(SELECTORS["job_area"]).first.inner_text()

            return Job(
                platform=getattr(self, "name", "boss"),
                job_id=match.group(1),
                company=company.strip(),
                title=title.strip(),
                city=(query.city or area.strip().split("·")[0]),
                salary_raw=salary.strip(),
                url=link if link.startswith("http") else BASE + link,
            )
        except Exception:
            return None

    # ---------------- 投递 ----------------

    def apply(self, job: Job) -> ApplyResult:
        started = time.time()
        self._applied.add(job.job_id)

        page = self._page
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        if page.locator(SELECTORS["captcha"]).count() > 0:
            return ApplyResult(ApplyStatus.NEEDS_HUMAN, "出现验证码，需人工处理")

        # 已是「已沟通/已投递」状态：不重复投
        button = page.locator(SELECTORS["apply_button"]).first
        if button.count() == 0:
            return ApplyResult(ApplyStatus.FAILED, "未找到投递按钮，页面结构与预期不符")

        label = (button.inner_text() or "").strip()
        if any(word in label for word in ("继续沟通", "已沟通", "已投递")):
            return ApplyResult(ApplyStatus.DUPLICATE, f"页面状态为「{label}」")

        button.click()
        page.wait_for_timeout(1500)

        # 必须等到页面给出明确成功态，等不到就认失败
        deadline = time.time() + 6
        while time.time() < deadline:
            if page.locator(SELECTORS["captcha"]).count() > 0:
                return ApplyResult(ApplyStatus.NEEDS_HUMAN, "点击后出现验证码")

            current = (page.locator(SELECTORS["apply_button"]).first.inner_text() or "").strip()
            if any(word in current for word in ("继续沟通", "已沟通", "已投递")):
                evidence = f"data/evidence/boss/{job.job_id}.png"
                page.screenshot(path=evidence)
                return ApplyResult(
                    ApplyStatus.SUCCESS,
                    f"页面状态变为「{current}」",
                    evidence=evidence,
                    elapsed_ms=int((time.time() - started) * 1000),
                )
            time.sleep(0.5)

        return ApplyResult(
            ApplyStatus.FAILED,
            "点击后未检测到状态变化",
            elapsed_ms=int((time.time() - started) * 1000),
        )

    # ---------------- 消息（只读） ----------------

    def inbox(self, since: Optional[str] = None) -> list[Message]:
        """只读会话列表。不自动回复、不自动同意好友。"""
        self._page.goto(f"{BASE}/web/geek/chat", wait_until="domcontentloaded")
        self._page.wait_for_timeout(1200)

        messages: list[Message] = []
        items = self._page.locator(".user-list li, .chat-user-list li")
        for i in range(min(items.count(), 30)):
            try:
                item = items.nth(i)
                preview = (item.inner_text() or "").strip().replace("\n", " ")[:80]
                messages.append(
                    Message(
                        platform=getattr(self, "name", "boss"),
                        message_id=f"boss-{i}",
                        kind="reply",
                        preview=preview,
                        needs_reply=True,
                    )
                )
            except Exception:
                continue
        return messages

    def close(self) -> None:
        # 保留登录态：只断开 CDP，不关浏览器、不清 Cookie
        try:
            if self._pw:
                self._pw.stop()
        finally:
            self._page = None
            self._browser = None
            self._pw = None
