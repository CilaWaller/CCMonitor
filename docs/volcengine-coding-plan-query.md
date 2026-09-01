# 火山方舟 Coding Plan 额度查询方法

> 用途：AI Coding Plan 剩余额度监控软件 —— 火山方舟查询通道技术文档
> 参考来源：[ccswitch-volcengine-coding-plan-usage](https://github.com/Aitidi/ccswitch-volcengine-coding-plan-usage)（CC Switch 已验证方案）
> 更新时间：2026-08-31

---

## 1. 概述

火山方舟（Volcano Engine Ark）Coding Plan 的套餐用量**没有公开文档化的 API**，但控制台页面内部调用了一个接口 `GetCodingPlanUsage`，带上浏览器登录 Cookie 即可直连查询。

- 查询粒度：**5 小时 / 7 天 / 30 天** 三档
- 返回内容：每档已用百分比、剩余百分比、下次刷新时间戳
- 认证方式：**Cookie 直连**（最小 Cookie 只需 `digest` + `csrfToken` 两个字段）
- 备选方式：子用户 AK/SK（`ArkReadOnlyAccess` + `BillingCenterReadOnlyAccess`）走官方签名 API，更稳定但配置繁琐

---

## 2. 获取 Cookie（一次性操作，Cookie 过期后需重取）

1. 浏览器登录火山引擎控制台，打开 Ark Coding Plan 页面：

   ```
   https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement?LLM=%7B%7D&advancedActiveKey=subscribe
   ```

2. 按 `F12` 打开开发者工具 → **Network / 网络** 标签
3. 刷新页面，在过滤框搜索 `GetCodingPlanUsage`
4. 点击该请求 → **Headers / 标头** → 复制 **Request Headers** 里的完整 `Cookie` 值
5. 提取最小 Cookie（只需保留两个字段）：

   ```
   digest=xxxxx; csrfToken=xxxxx
   ```

> ⚠️ Cookie 有有效期（一般数天~数周）。软件中应检测 401/登录跳转并提示用户重新抓取。

---

## 3. 接口定义

| 项目 | 内容 |
|------|------|
| URL | `https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01/GetCodingPlanUsage` |
| 方法 | `POST` |
| Body | `{"ProjectName": "default"}` |
| Content-Type | `application/json` |

### 必需请求头

```http
Content-Type: application/json
Accept: application/json, text/plain, */*
Accept-Language: zh-CN
Origin: https://console.volcengine.com
Referer: https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement?LLM=%7B%7D&advancedActiveKey=subscribe
Cookie: digest=xxx; csrfToken=xxx
X-Csrf-Token: <与 Cookie 中 csrfToken 相同的值>
User-Agent: Mozilla/5.0
```

> 注意：`X-Csrf-Token` 的值 = Cookie 中 `csrfToken` 字段的值，二者缺一不可。

---

## 4. 响应结构

```json
{
  "Result": {
    "QuotaUsage": [
      {
        "Level": "session",          // 档位: session=5小时 / weekly=7天 / monthly=30天
        "Percent": 42.5,             // 已使用百分比 (0~100)
        "ResetTimestamp": 1793000000000  // 下次刷新时间戳(毫秒；秒级时需×1000)
      }
      // ... 共 3 项
    ]
  }
}
```

### 字段解析要点

- `Level` → 显示名映射：`session`→「5小时」、`weekly`→「7天」、`monthly`→「30天」
- `Percent` → 已用 %；剩余 = `100 - Percent`
- `ResetTimestamp` → 兼容三种字段名回退：`ResetTimestamp` / `ResetTime` / `NextResetTime`；若数值 `< 1000000000000` 判定为秒级，需 ×1000 转毫秒
- 倒计时 = `ResetTimestamp - 当前时间`，格式化为 `X天X小时X分钟后刷新`

---

## 5. Python 参考实现

```python
import time
import requests

COOKIE = "digest=xxx; csrfToken=xxx"   # 从浏览器抓取后替换

URL = ("https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01/"
       "GetCodingPlanUsage")

def csrf(cookie: str) -> str:
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "csrfToken":
            return v
    return ""

def query_usage() -> list[dict]:
    resp = requests.post(
        URL,
        json={"ProjectName": "default"},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN",
            "Origin": "https://console.volcengine.com",
            "Referer": "https://console.volcengine.com/ark/region:ark+cn-beijing"
                       "/openManagement?LLM=%7B%7D&advancedActiveKey=subscribe",
            "Cookie": COOKIE,
            "X-Csrf-Token": csrf(COOKIE),
            "User-Agent": "Mozilla/5.0",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    usage = (data.get("Result") or data).get("QuotaUsage") or []

    level_name = {"session": "5小时", "weekly": "7天", "monthly": "30天"}
    result = []
    for item in usage:
        ts = int(item.get("ResetTimestamp") or item.get("ResetTime")
                 or item.get("NextResetTime") or 0)
        if 0 < ts < 1000000000000:
            ts *= 1000
        used = float(item.get("Percent") or 0)
        reset_in = max(0, ts - int(time.time() * 1000))
        result.append({
            "level": item.get("Level"),
            "name": level_name.get(item.get("Level"), item.get("Level")),
            "used_pct": round(used, 1),
            "remaining_pct": round(max(0.0, 100 - used), 1),
            "reset_in_seconds": reset_in // 1000,
        })
    return result

if __name__ == "__main__":
    for row in query_usage():
        print(f"{row['name']:>4}: 已用 {row['used_pct']}% | "
              f"剩余 {row['remaining_pct']}% | "
              f"{row['reset_in_seconds']//3600}h"
              f"{row['reset_in_seconds']%3600//60}m 后刷新")
```

预期输出示例：

```
 5小时: 已用 42.5% | 剩余 57.5% | 2h18m 后刷新
   7天: 已用 66.0% | 剩余 34.0% | 12h40m 后刷新
  30天: 已用 55.0% | 剩余 45.0% | 3d05h 后刷新
```

---

## 6. 展示与告警规则（与 CC Switch 对齐）

| 使用率 | 颜色 | 含义 |
|--------|------|------|
| < 70% | 🟢 绿 | 正常 |
| 70% – 89% | 🟠 橙 | 偏高，建议关注 |
| ≥ 90% | 🔴 红 | 接近耗尽，触发告警 |

- 刷新间隔建议：**5~10 分钟**（`0` 表示禁用自动刷新；查询本身会消耗少量配额，不宜过频）
- Cookie 失效特征：HTTP 401、返回体含登录跳转/`NeedLogin`，软件应置卡片为「会话已过期」状态

---

## 7. 常见问题

| 现象 | 排查 |
|------|------|
| 401 / 返回登录页 | Cookie 过期，重新抓取 `digest` + `csrfToken` |
| `QuotaUsage` 为空 | 确认账号已订阅 Coding Plan；`ProjectName` 用 `default` |
| 403 CSRF 校验失败 | `X-Csrf-Token` 与 Cookie 中 `csrfToken` 不一致 |
| 时间戳显示异常 | 判断秒/毫秒：`< 1000000000000` 视为秒级 ×1000 |
