# CCMonitor

AI Coding Plan 余量监控工具 —— 在 Windows 任务栏上叠加显示多个 AI 编程服务的剩余额度，实时掌握用量状态。

## 功能

- **任务栏悬浮条**：GDI+ 圆角深色卡片，叠加在任务栏上，多供应商分段显示（状态圆点 + 名称 + 余量），颜色随用量变化（🟢 <70% · 🟠 70–89% · 🔴 ≥90%），宽度自适应、超宽自动换行
- **系统托盘图标**：最小化后驻留托盘，悬停查看余量摘要，左键恢复窗口，右键菜单（打开 / 刷新 / 退出）
- **多供应商支持**：
  | 供应商 | 认证方式 | 显示内容 |
  |---|---|---|
  | 火山方舟 Coding Plan | AK/SK V4 签名 | 5小时 / 7天 / 30天 窗口 |
  | OpenCode Go | Bearer API Key | 5小时 / 每周 / 每月 窗口 |
  | OpenAI Codex (ChatGPT Plan) | OAuth（读取 `~/.codex/auth.json`，自动刷新 token） | 5小时 / 7天 / 代码审查周限 + 积分余额 |
  | DeepSeek 开放平台 | Bearer API Key | 账户余额（赠金 + 充值） |
- **自动代理探测**：国外接口（chatgpt.com / opencode.ai）自动走系统代理或常见本地端口（7897 / 7890 / 10809 / 1080 …），国内接口直连，无需手动配置
- **自动刷新**：可配置间隔轮询，悬浮条与托盘提示实时更新
- **设置界面**：Web UI 管理，支持启用/停用供应商、拖拽排序卡片、配置凭据、测试连接
- **单实例保护**：重复启动 exe 会唤回已运行的窗口而非新开实例

## 快速开始

### 方式一：直接运行 Python

```bash
pip install -r requirements.txt
python app.py
```

首次启动会在程序目录生成 `config.json`（空凭据），在设置页面填入后即可使用。

### 方式二：打包为 exe

```bash
build.bat
```

生成的 `dist\CCMonitor.exe` 可独立分发（含 UI 资源和图标）。

## 配置说明

程序运行后在同级目录生成 `config.json`，参考 `config.example.json`：

| 字段 | 说明 |
|---|---|
| `refresh_minutes` | 自动刷新间隔（分钟），0 = 关闭 |
| `background` | 最小化开关：开启时点击 × 隐藏到托盘（悬浮条保持）；关闭时点击 × 完全退出 |
| `proxy` | 手动代理地址（如 `http://127.0.0.1:7897`），留空 = 自动探测 |
| `bar.enabled` | 是否显示任务栏悬浮条 |
| `bar.opacity` | 悬浮条不透明度（30–100） |
| `bar.font_size` | 悬浮条字号（8–28） |
| `bar.items` | 悬浮条中显示的供应商列表 |
| `providers.*` | 各供应商凭据与启用状态 |

### 代理说明

访问 `chatgpt.com` / `opencode.ai` 需要代理。程序会自动探测：

1. Windows 系统代理（注册表 `ProxyEnable` → `ProxyServer`）
2. 常见本地端口（7897 / 7890 / 10809 / 1080 / 2080 / 8118）

探测成功缓存 5 分钟，失败 60 秒后自动重试。也可在设置页手动指定代理地址。

## 技术栈

- Python 3.10+
- Flask（本地 HTTP 服务） + pywebview（原生窗口）+ Win32 ctypes（悬浮条 / 托盘图标）
- 前端：原生 HTML / CSS / JavaScript（无框架）
- 打包：PyInstaller（onefile + noconsole）

## 项目结构

```
CCMonitor/
├── app.py                 # 全部后端逻辑（供应商查询、悬浮条、托盘、窗口管理）
├── app.ico                # 应用图标
├── config.example.json    # 配置示例（空凭据）
├── requirements.txt
├── build.bat              # PyInstaller 构建脚本
├── ui/
│   ├── index.html         # 主页面（余量卡片 + 设置）
│   ├── app.js             # 前端逻辑
│   └── style.css          # 样式
└── docs/
    ├── codex-余量查询方法.md
    ├── opencode-go-余量查询方法.md
    └── volcengine-coding-plan-query.md
```

## 免责声明

- 本工具调用各供应商的非公开/半公开 API，接口可能随时变更，届时需更新代码
- `chatgpt.com` 后端用量端点为非公开接口，使用风险自负
- 请妥善保管你的 API Key / AK / SK，切勿将含凭据的 `config.json` 提交到公开仓库

## License

MIT
