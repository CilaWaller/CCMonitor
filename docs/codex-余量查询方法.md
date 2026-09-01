# OpenAI Codex 余量查询方法

> 用途：AI Coding Plan 剩余额度监控软件 —— OpenAI Codex（ChatGPT 订阅）查询通道技术文档
> 参考来源：openusage-community/openusage · @willh/codex-reset-checker（多源交叉验证）
> 验证状态：✅ 已在 CCMonitor v1.3 中用真实凭据验证成功（2026-08-31）
> 更新时间：2026-08-31

---

## 1. 概述

Codex（ChatGPT Plus/Pro 订阅版）的用量通过 ChatGPT 后端**非公开端点**查询，OAuth 凭据由 Codex CLI 本地保存。

- 查询粒度：**5 小时滚动**（primary）+ **7 天**（secondary）双窗口；部分套餐另有**代码审查周限**
- 返回内容：各窗口已用百分比、重置时间戳（unix 秒）、套餐档位、可选积分余额
- 认证方式：`~/.codex/auth.json` 中的 OAuth access_token（Bearer）
- ⚠️ 非官方文档化接口，格式可能随 Codex 客户端版本变化

---

## 2. 凭据获取（零操作：CCMonitor 自动读取）

Codex CLI 登录后自动保存 OAuth 凭据（Windows 默认 `C:\Users\<你>\.codex\auth.json`）：

```json
{
  "OPENAI_API_KEY": null,
  "tokens": {
    "access_token": "eyJ...",       // Bearer 令牌（短时有效 JWT）
    "refresh_token": "rt_...",      // 用于刷新
    "id_token": "eyJ...",
    "account_id": "..."             // 作为 ChatGPT-Account-Id 请求头
  },
  "last_refresh": "2026-01-28T08:05:37Z"
}
```

> ⚠️ 凭据存储模式：file（默认）/ keyring（凭据管理器）/ auto。若使用 keyring 模式且磁盘上无 auth.json，CCMonitor 无法自动读取。
> 🔒 auth.json 等同私钥，勿提交 git、勿分享；泄露后立即 `codex --logout`。

---

## 3. 接口定义

| 项目 | 内容 |
|------|------|
| URL | `https://chatgpt.com/backend-api/wham/usage` |
| 方法 | `GET` |
| Authorization | `Bearer <tokens.access_token>` |
| Accept | `application/json` |
| originator | `Codex Desktop` |
| ChatGPT-Account-Id | `tokens.account_id`（存在时携带） |

### 令牌刷新（401 时）

```http
POST https://auth.openai.com/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&client_id=app_EMoamEEZ73f0CkXaXp7hrann&refresh_token=<rt>
```

- `client_id` 为 Codex CLI 官方 OAuth client（`app_EMoamEEZ73f0CkXaXp7hrann`）
- 返回新 `access_token`（可能附带新 `refresh_token`/`id_token`），需写回 auth.json
- CCMonitor 已实现：401 → 自动刷新 → 重试一次 → 刷新失败则提示重新 `codex login`

---

## 4. 响应结构

```json
{
  "plan_type": "plus",
  "rate_limit": {
    "primary_window":   { "used_percent": 6,  "reset_at": 1738300000, "limit_window_seconds": 18000 },
    "secondary_window": { "used_percent": 24, "reset_at": 1738900000, "limit_window_seconds": 604800 }
  },
  "code_review_rate_limit": {
    "primary_window": { "used_percent": 0, "reset_at": 1738900000, "limit_window_seconds": 604800 }
  },
  "credits": { "has_credits": true, "unlimited": false, "balance": 5.39 }
}
```

### 字段解析要点

- `used_percent` = 已用 %；剩余 = `100 - used_percent`
- `reset_at` = unix **秒**；CCMonitor 统一 ×1000 转毫秒做倒计时
- `limit_window_seconds`：18000 = 5小时 / 604800 = 7天
- 双窗口同时生效，任一触顶即限流
- `plan_type`：plus / pro / prolite / free 等；prolite 等套餐可能只返回 primary_window（5h），secondary 缺失时跳过
- `credits.balance` = 积分余额（可选字段）

---

## 5. 实测记录（CCMonitor v1.3，2026-08-31）

```
codex status: ok
套餐: prolite
5小时: 已用 0.0% | 剩余 100.0% | reset 时间正常
```

- prolite 套餐实测仅返回 primary_window（5 小时），无 secondary
- 无效/过期令牌返回 401 → 触发自动刷新

---

## 6. 常见问题

| 现象 | 排查 |
|------|------|
| 「未找到凭据文件」 | 未安装 Codex CLI 或未登录 → 运行 `codex login` |
| 「登录态已失效」 | refresh_token 也失效 → 重新 `codex login` |
| keyring 模式无 auth.json | 在 `~/.codex/config.toml` 设 `cli_auth_credentials_store = "file"` 后重新登录 |
| 窗口数据缺失 | 非官方接口，响应格式可能变化；对照第 4 节检查 |
