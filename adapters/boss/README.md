# BOSS 直聘适配器（参考实现）

**状态：未实测。** 选择器与流程来自人工跑通的真实页面，平台改版后需校准。
第一次用请只跑 `--dry-run`，核对 `search()` 取到的岗位、标题、公司、链接都与页面一致。

## 依赖

```bash
python -m pip install ".[browser]"
python -m playwright install chromium
```

## 前置条件

本机 Edge/Chrome 以调试端口启动并已手动登录 BOSS：

```bash
# Windows 示例
msedge.exe --remote-debugging-port=9222 --user-data-dir=<你的profile目录>
```

适配器只接管这个已登录会话，不登录、不处理验证码。

## 配置

```yaml
platforms:
  boss:
    enabled: true
    module: adapters/boss/adapter.py
    options:
      cdp_url: "http://127.0.0.1:9222"
    query:
      keywords: "质量总监"
      city: "台州"
      pages: 2
    pacing:
      platform_daily_cap: 30
```

## 语义提醒

BOSS 的「投递」实际是发起沟通（立即沟通）。所以：

- `SUCCESS` 的判据是按钮变成「继续沟通」并留下截图证据
- 不要把它算成"投递简历"，战报里口径写「发起沟通 N 个」更准确

## 坑

见 [`docs/case-boss.md`](../../docs/case-boss.md)：视口高度、动态坐标、日期面板翻十年、三级城市联动、导入弹层不能用导航刷新。
