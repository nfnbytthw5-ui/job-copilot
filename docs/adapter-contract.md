# 平台适配器接口约定

适配器是**唯一允许产生副作用**的地方。它只做两件事：把平台页面翻译成标准数据结构，把标准投递指令翻译成页面动作。所有判断（要不要投、还能不能投、投没投过）都不在适配器里做。

这个边界很重要：它让「换一个平台」变成写一个适配器，而不是重写一遍流程；也让去重、限速、台账这些真正有价值的部分与平台解耦。

---

## 1. 协议

```python
from typing import Protocol, Iterable

class Adapter(Protocol):
    name: str                       # 平台标识，小写，与 config 中的 key 一致
    display_name: str               # 展示名

    def open(self, ctx: "RunContext") -> None:
        """接管一个已登录的浏览器会话。不得在此处执行登录、输入密码或处理验证码。"""

    def health(self) -> "Health":
        """返回登录态、页面结构、剩余额度。orchestrator 在每批开始前调用一次。"""

    def search(self, query: "Query") -> Iterable["Job"]:
        """按条件取岗。必须是无副作用的只读操作，不得触发投递或打招呼。"""

    def apply(self, job: "Job") -> "ApplyResult":
        """对单个岗位执行投递。必须幂等：同一 job_id 第二次调用返回 DUPLICATE，不得真投。"""

    def inbox(self, since: str | None) -> Iterable["Message"]:
        """拉取站内消息（HR 回复、邀约、系统通知）。只读，不得自动回复。"""

    def close(self) -> None:
        """释放会话，保留登录态。不得清 Cookie。"""
```

## 2. 六个方法的硬性要求

| 方法 | 可以做什么 | 绝对不做 |
|---|---|---|
| `open` | 连接已登录标签页、校验身份、记录账号指纹 | 登录、输密码、过验证码、切账号 |
| `health` | 读页面状态、判断是否掉登录、检查关键选择器是否存在 | 修复页面、重试登录 |
| `search` | 翻页、读卡片、解析字段 | 点「立即沟通」「投递」「打招呼」 |
| `apply` | 点投递、确认弹窗、留存证据 | 改简历、改期望、批量勾选投递 |
| `inbox` | 读消息列表与正文 | 自动回复、自动同意好友、自动加微信 |
| `close` | 关闭页面、落盘会话信息 | 清 Cookie、退登录 |

## 3. 数据模型

```python
@dataclass
class Job:
    platform: str          # "boss"
    job_id: str            # 平台内的稳定唯一 ID，优先用 URL 中的 ID，其次用详情页 ID
    company: str           # 原始公司名，保留后缀，归一化交给 ledger 做
    title: str             # 原始岗位名
    city: str              # 城市，统一到「市」级，不带区县
    salary_raw: str        # 原始薪资文案，如 "35-55K·14薪"，不要自己换算
    url: str               # 详情页 URL，用于人工复核
    posted_at: str | None  # ISO 8601，拿不到就 None，不要瞎猜
    tags: list[str]        # 平台标签（行业/规模/融资阶段），拿不到给空列表
    jd_hash: str           # JD 正文的 sha1 前 16 位，用于识别"同名不同岗"
```

`job_id` 的三条规则：

- **必须稳定**：同一个岗位今天和明天取到，`job_id` 必须一致。用自增序号、列表下标都是错的。
- **必须可追溯**：从 `job_id` 应能拼出详情页 URL，或至少能通过 `url` 字段打开。
- **跨平台不通用**：不同平台的 `job_id` 命名空间独立，不要试图对齐。

`jd_hash` 的用途：有些平台同一个 `job_id` 会被 HR 反复改标题重发，或同一家公司挂多个几乎相同的岗位。标题+公司相同但 `jd_hash` 不同 → 视为两个岗位；反过来 `jd_hash` 相同但 `job_id` 不同 → 大概率是重复挂岗，交给去重策略处理（默认按重复算）。

## 4. 投递结果

```python
class ApplyStatus(str, Enum):
    SUCCESS      = "success"       # 页面明确出现投递成功/已投递状态
    DUPLICATE    = "duplicate"     # 平台显示"已投递"，或台账判定重复，未产生新投递
    BLOCKED      = "blocked"       # 命中黑名单或硬性条件，主动跳过
    SKIPPED      = "skipped"       # 岗位已下线、薪资不符、地点不符等
    FAILED       = "failed"        # 点击了但没成功，原因可归因（元素消失、超时、弹窗拦截）
    NEEDS_HUMAN  = "needs_human"   # 需要人介入：验证码、异常弹窗、登录失效、页面改版

@dataclass
class ApplyResult:
    status: ApplyStatus
    reason: str                    # 一句话，写清"为什么是这个状态"
    evidence: str | None           # 成功/失败证据文件路径（截图或 DOM 快照）
    elapsed_ms: int
```

**状态判定以页面证据为准，不以代码自认为准。** 点了按钮不等于投递成功：

- `SUCCESS` 必须有可核验的证据：按钮变成「已投递」、页面出现成功 toast、或消息列表出现对应会话。三者至少命中一个，并把证据落到 `evidence`。
- 点击后无任何状态变化 → `FAILED`，不要记 `SUCCESS`。这是这类脚本最常见的假账来源。
- 出现验证码/滑块/异地登录提示 → 立刻 `NEEDS_HUMAN`，并让整个适配器进入暂停态，不做任何重试。

## 5. 幂等要求

平台侧的「已投递」状态可能延迟出现，也可能因为页面缓存看不到。所以幂等必须双保险：

1. **平台侧检查**：`apply` 开头先读一次岗位卡片状态，已是「已投递」直接返回 `DUPLICATE`。
2. **台账侧检查**：orchestrator 在调用 `apply` 之前已按 `platform + job_id` 查过台账，命中则根本不调用。

适配器不要自己碰台账文件，它只负责返回准确的状态。

## 6. 会话与账号

- 登录态由用户在自己的浏览器里完成，程序只接管**已登录**会话。
- 适配器可以读账号标识（用于台账区分账号），但**不得写入日志、不得上报**。
- 同一时刻一个适配器只允许一个会话，避免并发点击导致状态错乱。
- `close` 不清 Cookie。下次启动直接复用，减少登录频次本身就是一种降风险。

## 7. 页面改版的处理

平台改版是必然事件，把它当正常路径处理：

- 关键选择器集中定义在适配器顶部的 `SELECTORS` 常量区，改版只改这一处。
- `health()` 里做**选择器存活检查**：核心的 3-5 个选择器任一失效，返回 `DEGRADED`，orchestrator 直接停该平台，不进入投递。
- 报错信息里带上「哪个选择器失效」，而不是笼统的 `TimeoutException`。这决定了你第二天能不能五分钟修好。

## 8. 新增适配器的自检清单

提 PR 前逐条过：

- [ ] `search()` 全程无副作用，跑一遍不会产生任何投递或消息
- [ ] `job_id` 稳定：同一岗位连续两天取到，ID 一致
- [ ] `apply()` 对同一 `job_id` 连续调用两次，第二次返回 `DUPLICATE`
- [ ] 每条 `SUCCESS` 都有 `evidence` 文件，且文件能打开
- [ ] 验证码/滑块路径返回 `NEEDS_HUMAN`，且不会自动重试
- [ ] 代码里没有硬编码手机号、邮箱、Cookie、绝对路径
- [ ] 默认配置下首批只跑 3 个，人工核对页面确有投递记录
