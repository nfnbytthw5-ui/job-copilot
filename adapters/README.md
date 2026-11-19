# 适配器

每个平台一个目录，目录内 `adapter.py` 暴露 `Adapter` 类。

- `_template/` — 从这里复制，不要直接改
- `mock/` — 离线演示，不访问网络，用于跑通流水线

## 新增流程

```
cp -r adapters/_template adapters/yourplatform
# 实现 6 个方法 → 在 config.yaml 注册 → 先 --dry-run 跑通 → 首批真实投 3 个核对
```

适配器里不允许出现：账号密码、Cookie 明文、手机号、邮箱、绝对路径。登录态由用户在浏览器里手动建立，程序只接管已登录会话。
