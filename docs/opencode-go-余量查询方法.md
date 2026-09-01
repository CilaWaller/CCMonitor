# OpenCode Go 余量查询方法

> 整理日期：2026-08-31
> 适用对象：OpenCode Go 订阅用户
> 官方文档：https://opencode.ai/docs/go

OpenCode Go 的用量/余量通过 **rolling（5 小时）/ weekly（每周）/ monthly（每月）** 三个窗口计量。以下三种查询方式按场景选用。

---

## 方式一：官方内置查询（最简单，无需额外工具）

在 OpenCode 对话中直接询问：

```
我的 OpenCode Go 套餐还有多少用量？
```

或在浏览器直接打开官方用量页面：

```
https://opencode.ai/workspace/{WORKSPACE_ID}/go
```

**特点**：零配置，随开随看；适合日常快速查看。

---

## 方式二：安装插件 opencode-go-usage（终端内随时查看）

### 1. 安装插件

编辑 `~/.config/opencode/opencode.json`：

```json
{
  "plugin": ["opencode-go-usage"]
}
```

OpenCode 下次启动时会自动通过 Bun 安装插件。

### 2. 配置凭据（推荐环境变量）

```bash
export OPENCODE_GO_WORKSPACE_ID="wrk_你的WORKSPACE_ID"
export OPENCODE_GO_AUTH_COOKIE="Fe26.2**你的auth cookie"
export OPENCODE_GO_REFRESH_MINUTES=5
export OPENCODE_GO_SHOW_AT_START=true
```

写入 `~/.bashrc` / `~/.zshrc` 生效。

### 3. 使用方式

- 命令：在 OpenCode 提示符输入 `/ogc-usage`
- 自然语言：`Show me my OpenCode Go quota`

### 输出示例

```
OpenCode Go Usage:
Rolling: 0% (resets in 4h 57m)
Weekly:  17% (resets in 2d 18h)
Monthly: 8%  (resets in 29d 22h)
```

🟢 绿色 ≤80% 用量；超过后变色提示。

---

## 方式三：官方 API 端点直接调用（适合脚本化/监控）

OpenAI 兼容网关的用量接口：

```bash
curl https://opencode.ai/zen/go/v1/usage \
  -H "Authorization: Bearer YOUR_OPENCODE_GO_API_KEY"
```

返回结构：

```json
{
  "usage": {
    "rolling":  { "status": "ok", "percent": 1,  "resetsAt": "2026-08-31T18:00:00Z" },
    "weekly":   { "status": "ok", "percent": 12, "resetsAt": "..." },
    "monthly":  { "status": "ok", "percent": 35, "resetsAt": "..." }
  }
}
```

**字段说明**：
- `percent` = 该窗口**已使用**百分比；`100 - percent` = 剩余额度
- `status` = `ok`（正常）或其它（超额时提示注意）
- `resetsAt` = 窗口重置时间（ISO 8601，本地化显示即可）
- 无效密钥 → HTTP 401

---

## 附：MCP 方式（opencode-balance-mcp，可查 Go 配额 + Zen 余额）

若需在任意 MCP 客户端（Claude Code / Cursor / opencode 等）中查询：

```bash
npx -y opencode-balance-mcp --workspace-id wrk_xxx --auth-cookie "Fe26.2**..."
```

提供两个工具：
- `query_go_usage` — 三档 Go 配额（rolling/weekly/monthly）
- `query_zen_balance` — Zen 预付余额（含自动充值设置）

---

## 凭据获取方法

| 凭据 | 格式 | 获取方式 |
|------|------|----------|
| **Workspace ID** | `wrk_xxx` | workspace URL 中：`https://opencode.ai/workspace/{WORKSPACE_ID}/go` |
| **Auth Cookie** | 以 `Fe26.2**` 开头 | 浏览器 F12 → Application → Cookies → 复制 `auth` 的值 |
| **API Key** | Bearer Token | 环境变量 `OPENCODE_GO_API_KEY`（官方 opencode 模型专用） |

> ⚠️ **注意**：
> - Auth Cookie 会过期，失效后重新登录 https://opencode.ai 并从浏览器获取新值。
> - 凭据建议用环境变量存储，不要提交到 git；如用配置文件，设 `chmod 600`。
> - 所有官方查询仅访问 opencode.ai 域名，HTTPS 请求。

---

## 方式选择建议

| 场景 | 推荐方式 |
|------|----------|
| 日常快速查看 | 方式一（对话询问 / 官网页面） |
| 终端内随时查看、会话开始自动显示 | 方式二（插件） |
| 脚本监控、报警、集成到自建系统 | 方式三（API） |
| 多客户端统一查询 + Zen 余额 | MCP 方式 |
