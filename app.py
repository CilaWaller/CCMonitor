# -*- coding: utf-8 -*-
"""
CCMonitor —— AI Coding Plan 剩余额度监控
供应商支持：火山方舟 Coding Plan（AK/SK 签名）/ OpenCode Go / OpenAI Codex
架构：Flask 本地服务 + 原生应用窗口（pywebview），PyInstaller 打包为单文件 exe
"""
import ctypes
import json
import os
import sys
import time
import threading
import webbrowser
import traceback
import hashlib
import hmac
from ctypes import byref, wintypes
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request, send_from_directory

APP_NAME = "CCMonitor"
HOST = "127.0.0.1"
PORT = 18765
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- 配置读写

def app_dir() -> str:
    """exe 所在目录（打包后）或脚本目录（开发时），配置随目录可携带"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir() -> str:
    """只读资源目录：onefile 打包后解压在 sys._MEIPASS"""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")

DEFAULT_CONFIG = {
    "refresh_minutes": 5,          # 自动刷新间隔（分钟），0 = 关闭
    "background": True,            # 关闭主窗口后最小化到任务栏（悬浮条持续显示）
    "proxy": "",                   # 国外接口手动代理（如 http://127.0.0.1:7897），留空自动探测
    "card_order": [                # 卡片显示顺序（首页拖拽后更新）
        "volcengine", "opencode", "codex", "deepseek"],
    "bar": {
        "enabled": True,           # 启用任务栏悬浮条（叠加显示余量）
        "opacity": 92,             # 不透明度（百分比）
        "refresh_seconds": 30,     # 悬浮条文本刷新间隔（秒）
        "bg_color": "#1f2328",     # 背景色
        "text_color": "#ffffff",   # 文字颜色
        "font_size": 14,           # 字号
        "x": None,                 # 悬浮条位置（None=自动叠加在任务栏上居中）
        "y": None,
        "items": [                 # 在悬浮条中显示的供应商
            "volcengine", "opencode", "codex", "deepseek"],
    },
    "providers": {
        "volcengine": {
            "enabled": True,
            "name": "火山方舟 Coding Plan",
            "ak": "",                # Access Key ID
            "sk": "",                # Secret Access Key
            "project": "default",
        },
        "opencode": {
            "enabled": True,
            "name": "OpenCode Go",
            "api_key": "",
        },
        "codex": {
            "enabled": True,
            "name": "OpenAI Codex (ChatGPT Plan)",
            "auth_path": "",         # 留空 = 自动读取 ~/.codex/auth.json
            "account_id": "",        # 留空 = 自动取 auth.json 中的 account_id
        },
        "deepseek": {
            "enabled": True,
            "name": "DeepSeek 开放平台",
            "api_key": "",           # sk- 开头，platform.deepseek.com 创建
        },
    },
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        cfg["refresh_minutes"] = saved.get("refresh_minutes", cfg["refresh_minutes"])
        if isinstance(saved.get("background"), bool):
            cfg["background"] = saved["background"]
        if isinstance(saved.get("proxy"), str):
            cfg["proxy"] = saved["proxy"]
        if isinstance(saved.get("card_order"), list):
            cfg["card_order"] = [x for x in saved["card_order"]
                                 if x in cfg["providers"]]
            for pid in cfg["providers"]:      # 补全新增供应商
                if pid not in cfg["card_order"]:
                    cfg["card_order"].append(pid)
        if isinstance(saved.get("bar"), dict):
            cfg["bar"].update({k: v for k, v in saved["bar"].items()
                               if k in cfg["bar"]})
            if isinstance(saved["bar"].get("items"), list):
                cfg["bar"]["items"] = [
                    x for x in saved["bar"]["items"] if x in cfg["providers"]]
        for pid, p in saved.get("providers", {}).items():
            if pid in cfg["providers"]:
                cfg["providers"][pid].update(p)
    except Exception:
        pass
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CONFIG = load_config()

# ---------------------------------------------------------------- 工具

LEVEL_NAMES = {"session": "5小时", "weekly": "7天", "monthly": "30天",
               "rolling": "5小时"}


def to_ms(ts) -> int:
    """时间戳统一转毫秒；兼容秒级 / 毫秒级 / ISO8601 字符串"""
    if ts is None:
        return 0
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            try:
                return to_ms(float(s))
            except Exception:
                return 0
    try:
        v = float(ts)
    except Exception:
        return 0
    if 0 < v < 1000000000000:   # 秒级 → 毫秒
        v *= 1000
    return int(v)


def first_key(d: dict, keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def make_window(level, used_pct, reset_ms=0, extra=None):
    used = round(max(0.0, min(100.0, float(used_pct or 0))), 1)
    w = {
        "level": level,
        "name": LEVEL_NAMES.get(level, level or "窗口"),
        "used_pct": used,
        "remaining_pct": round(100.0 - used, 1),
        "reset_ms": int(reset_ms),
    }
    if extra:
        w.update(extra)
    return w


def ok_result(windows, note=None):
    return {"status": "ok", "windows": windows, "note": note, "error": ""}


def err_result(msg, status="error"):
    return {"status": status, "windows": [], "note": "", "error": msg}


def _net_err(e) -> str:
    """网络异常 → 可读的中文提示"""
    s = str(e)
    if "getaddrinfo" in s or "NameResolution" in s:
        return ("DNS 解析失败：本机当前网络无法访问该域名"
                "（境内网络需开启代理后重试）")
    if "timed out" in s or "Timeout" in s:
        return "连接超时：网络不通或代理未开启"
    if "Connection refused" in s or "ConnectionReset" in s or "10061" in s:
        return "连接被拒绝：代理端口已失效，请确认代理工具正在运行"
    return f"网络请求失败：{s}"


# ------- 国外接口代理（chatgpt.com / opencode.ai 等境内无法直连）-------

_PROXY_CACHE = {"value": None, "ts": 0.0}    # 自动探测结果缓存
_PROXY_CANDIDATES = ("http://127.0.0.1:7897", "http://127.0.0.1:7890",
                     "http://127.0.0.1:10809", "http://127.0.0.1:1080",
                     "http://127.0.0.1:2080", "http://127.0.0.1:8118")
_FOREIGN_HOSTS = ("chatgpt.com", "openai.com", "opencode.ai")


def _validate_proxy(px: str) -> bool:
    try:
        r = requests.get("https://www.gstatic.com/generate_204",
                         proxies={"http": px, "https": px}, timeout=4)
        return r.status_code == 204
    except Exception:
        return False


def _detect_proxy():
    """自动探测可用代理：系统代理(ProxyEnable) → 常见本地端口；成功缓存 5 分钟，失败 60 秒"""
    now = time.time()
    ttl = 300 if _PROXY_CACHE["value"] else 60
    if now - _PROXY_CACHE["ts"] < ttl:
        return _PROXY_CACHE["value"]

    found = None
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            enable, _ = winreg.QueryValueEx(k, "ProxyEnable")
            if enable:
                server = winreg.QueryValueEx(k, "ProxyServer")[0]
                if "=" in server:            # 兼容 "http=...;https=..." 写法
                    for part in server.split(";"):
                        part = part.strip()
                        if part.startswith(("https=", "http=")):
                            server = part.split("=", 1)[1]
                            break
                if server:
                    cand = "http://" + server.lstrip("/")
                    if _validate_proxy(cand):
                        found = cand
    except Exception:
        pass

    if not found:
        for cand in _PROXY_CANDIDATES:
            if _validate_proxy(cand):
                found = cand
                break

    _PROXY_CACHE["value"] = found
    _PROXY_CACHE["ts"] = now
    if found:
        _diag(f"proxy auto-detected: {found}")
    return found


def _proxies_for(url: str):
    """决定某请求是否走代理：国内接口直连；国外接口 = 手动配置 > 自动探测 > 直连"""
    if not any(h in url for h in _FOREIGN_HOSTS):
        return None
    p = (CONFIG.get("proxy") or "").strip()
    if p:
        return {"http": p, "https": p}
    p = _detect_proxy()
    return {"http": p, "https": p} if p else None

# ---------------------------------------------------------------- 供应商查询实现

# ---------------- 火山引擎 V4 签名（HMAC-SHA256，标准 OpenAPI 签名协议） ----------------

def _v4_sign(method: str, host: str, path: str, query: dict,
             body: str, ak: str, sk: str,
             region: str = "cn-beijing", service: str = "ark") -> dict:
    """构造火山引擎 V4 签名请求头（等价官方 SignerV4，terminal service = request）"""
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_short = now.strftime("%Y%m%d")

    def uri_enc(s: str, safe: str = "-_.~") -> str:
        return quote(str(s), safe=safe)

    # CanonicalQueryString：按 key 排序
    qs = "&".join(
        f"{uri_enc(k)}={uri_enc(v)}" for k, v in sorted(query.items()))
    canonical_uri = path or "/"

    payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    headers = {
        "host": host,
        "x-content-sha256": payload_hash,
        "x-date": x_date,
    }
    if body:
        headers["content-type"] = "application/json"
    signed_names = sorted(headers.keys())
    canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in signed_names)
    signed_headers = ";".join(signed_names)

    canonical_request = "\n".join([
        method.upper(), canonical_uri, qs, canonical_headers, signed_headers,
        payload_hash])

    scope = f"{date_short}/{region}/{service}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(sk.encode("utf-8"), date_short)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    authorization = (f"HMAC-SHA256 Credential={ak}/{scope}, "
                     f"SignedHeaders={signed_headers}, Signature={signature}")
    out = {
        "Authorization": authorization,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Host": host,
        "Accept": "application/json",
        "User-Agent": UA,
    }
    if body:
        out["Content-Type"] = "application/json"
    return out


def _parse_volc_usage(data: dict):
    """解析 GetCodingPlanUsage 响应（Cookie / AK-SK 两种模式共用）"""
    if not isinstance(data, dict):
        return None
    usage = (data.get("Result") or data).get("QuotaUsage")
    if usage is None:
        return None
    windows = []
    for item in usage or []:
        ts = to_ms(first_key(item, ("ResetTimestamp", "ResetTime", "NextResetTime"), 0))
        windows.append(make_window(item.get("Level"), item.get("Percent"), ts))
    order = {"session": 0, "weekly": 1, "monthly": 2}
    windows.sort(key=lambda w: order.get(w["level"], 9))
    return windows


def _is_action_not_found(resp) -> bool:
    try:
        data = resp.json()
    except Exception:
        return False
    code = ((data.get("ResponseMetadata") or {}).get("Error") or {}).get("Code", "")
    return "notfound" in str(code).lower() or "invalidaction" in str(code).lower()


def query_volcengine(p: dict):
    """火山方舟：open.volcengineapi.com 官方网关 + V4 签名（AK/SK，待真实凭据验证）"""
    ak, sk = (p.get("ak") or "").strip(), (p.get("sk") or "").strip()
    if not ak or not sk:
        return err_result("未配置 Access Key / Secret Key")
    host = "open.volcengineapi.com"
    project = p.get("project") or "default"
    try:
        # 首选：GET Action 形式
        query = {"Action": "GetCodingPlanUsage", "Version": "2024-01-01",
                 "ProjectName": project}
        hdrs = _v4_sign("GET", host, "/", query, "", ak, sk)
        resp = requests.get(f"https://{host}/", params=query,
                            headers=hdrs, timeout=12)
        # 降级：POST JSON body 形式（部分 Action 仅接受 POST）
        if resp.status_code in (404, 405) or _is_action_not_found(resp):
            body = json.dumps({"ProjectName": project})
            hdrs = _v4_sign("POST", host, "/", {"Action": "GetCodingPlanUsage",
                                                "Version": "2024-01-01"},
                            body, ak, sk)
            resp = requests.post(
                f"https://{host}/?Action=GetCodingPlanUsage&Version=2024-01-01",
                data=body, headers=hdrs, timeout=12)
    except requests.RequestException as e:
        return err_result(f"网络请求失败：{e}")

    if resp.status_code in (401, 403):
        return err_result("签名/权限被拒（HTTP %d）：确认 AK/SK 有效且子账号具"
                          "有 Ark 只读权限" % resp.status_code, status="expired")
    try:
        data = resp.json()
    except ValueError:
        return err_result(f"响应不是有效 JSON（HTTP {resp.status_code}）")

    # 官方网关错误体：ResponseMetadata.Error
    emeta = (data.get("ResponseMetadata") or {}).get("Error") or {}
    if emeta:
        code = emeta.get("Code", "")
        if "notfound" in code.lower() or "invalidaction" in code.lower():
            return err_result(
                f"官方网关不支持该 Action（{code}）：Coding Plan 用量接口可能"
                "未开放 AK/SK 签名调用，请关注后续官方文档", status="unverified")
        return err_result(f"网关错误 {code}: {emeta.get('Message', '')}")

    windows = _parse_volc_usage(data)
    if windows is None:
        return err_result("响应中未找到 QuotaUsage，请核对响应内容", status="unverified")
    return ok_result(windows)


def query_opencode(p: dict):
    """OpenCode Go：官方用量 API（Bearer API Key）"""
    key = (p.get("api_key") or "").strip()
    if not key:
        return err_result("未配置 API Key")
    url = "https://opencode.ai/zen/go/v1/usage"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {key}",
                     "Accept": "application/json", "User-Agent": UA},
            timeout=12, proxies=_proxies_for(url))
    except requests.RequestException as e:
        return err_result(_net_err(e))
    if resp.status_code == 401:
        return err_result("API Key 无效（HTTP 401）", status="expired")
    if resp.status_code != 200:
        return err_result(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return err_result("响应不是有效 JSON")

    usage = data.get("usage") or {}
    windows, level_names = [], {"rolling": "5小时", "weekly": "每周", "monthly": "每月"}
    for lv in ("rolling", "weekly", "monthly"):
        it = usage.get(lv)
        if not isinstance(it, dict):
            continue
        windows.append(make_window(lv, it.get("percent"), to_ms(it.get("resetsAt")),
                                   {"name": level_names[lv]}))
    if not windows:
        return err_result("响应中无 rolling/weekly/monthly 字段，请核对 Key 是否为 Go 套餐")
    return ok_result(windows)


# ---------------- OpenAI Codex（ChatGPT 订阅） ----------------

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"   # Codex CLI 官方 OAuth client


def _codex_auth(p: dict):
    """读取 Codex 凭据：优先设置中的路径，否则 ~/.codex/auth.json"""
    path = (p.get("auth_path") or "").strip()
    if not path:
        path = os.path.join(os.path.expanduser("~"), ".codex", "auth.json")
    if not os.path.exists(path):
        return None, f"未找到 Codex 凭据文件：{path}（请先运行 codex login）"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"凭据文件读取失败：{e}"
    tokens = data.get("tokens") or {}
    if not tokens.get("access_token"):
        return None, "凭据文件中缺少 tokens.access_token（请重新 codex login）"
    return {"path": path, "data": data, "tokens": tokens}, ""


def _codex_refresh(auth: dict) -> bool:
    """用 refresh_token 刷新 access_token 并写回 auth.json（保留其余字段）"""
    rt = (auth["tokens"].get("refresh_token") or "").strip()
    if not rt:
        return False
    url = "https://auth.openai.com/oauth/token"
    try:
        resp = requests.post(
            url,
            data={"grant_type": "refresh_token", "client_id": CODEX_CLIENT_ID,
                  "refresh_token": rt},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=12, proxies=_proxies_for(url))
        j = resp.json()
        new_at = j.get("access_token")
        if not new_at:
            return False
        auth["tokens"]["access_token"] = new_at
        if j.get("refresh_token"):
            auth["tokens"]["refresh_token"] = j["refresh_token"]
        if j.get("id_token"):
            auth["tokens"]["id_token"] = j["id_token"]
        auth["data"]["last_refresh"] = datetime.now(timezone.utc).isoformat()
        with open(auth["path"], "w", encoding="utf-8") as f:
            json.dump(auth["data"], f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def query_codex(p: dict):
    """OpenAI Codex：GET chatgpt.com/backend-api/wham/usage（OAuth Bearer）"""
    auth, emsg = _codex_auth(p)
    if not auth:
        return err_result(emsg)
    account_id = (p.get("account_id") or "").strip() or \
                 auth["tokens"].get("account_id") or ""

    def _do(at: str):
        headers = {"Authorization": f"Bearer {at}",
                   "Accept": "application/json",
                   "originator": "Codex Desktop", "User-Agent": UA}
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        url = "https://chatgpt.com/backend-api/wham/usage"
        return requests.get(url, headers=headers, timeout=12,
                            proxies=_proxies_for(url))

    try:
        resp = _do(auth["tokens"]["access_token"])
        if resp.status_code in (401, 403) and _codex_refresh(auth):
            resp = _do(auth["tokens"]["access_token"])   # 刷新后重试一次
    except requests.RequestException as e:
        return err_result(_net_err(e))

    if resp.status_code in (401, 403):
        return err_result("登录态已失效（HTTP %d）：请先在 Codex CLI 中重新登录"
                          % resp.status_code, status="expired")
    if resp.status_code != 200:
        return err_result(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return err_result("响应不是有效 JSON")

    rl = data.get("rate_limit") or {}
    windows = []
    for key, name in (("primary_window", "5小时"), ("secondary_window", "7天")):
        w = rl.get(key)
        if isinstance(w, dict):
            windows.append(make_window(
                key, w.get("used_percent"), to_ms(w.get("reset_at")),
                {"name": name}))
    cr = data.get("code_review_rate_limit")
    if isinstance(cr, dict) and isinstance(cr.get("primary_window"), dict):
        pw = cr["primary_window"]
        windows.append(make_window("code_review", pw.get("used_percent"),
                                   to_ms(pw.get("reset_at")),
                                   {"name": "代码审查(周)"}))
    if not windows:
        return err_result("响应中无 rate_limit 窗口数据，请核对响应内容")

    notes = []
    if data.get("plan_type"):
        notes.append(f"套餐: {data['plan_type']}")
    credits = data.get("credits")
    if isinstance(credits, dict) and credits.get("has_credits") \
            and not credits.get("unlimited") and credits.get("balance") is not None:
        notes.append(f"积分余额: {credits['balance']}")
    return ok_result(windows, note=" · ".join(notes) or None)


def query_deepseek(p: dict):
    """DeepSeek 开放平台：官方余额接口 GET /user/balance（Bearer sk-key）"""
    key = (p.get("api_key") or "").strip()
    if not key:
        return err_result("未配置 API Key（sk- 开头，platform.deepseek.com 创建）")
    try:
        resp = requests.get("https://api.deepseek.com/user/balance",
                            headers={"Authorization": f"Bearer {key}",
                                     "Accept": "application/json",
                                     "User-Agent": UA}, timeout=12)
    except requests.RequestException as e:
        return err_result(_net_err(e))
    if resp.status_code == 401:
        return err_result("API Key 无效（HTTP 401）", status="expired")
    if resp.status_code == 402:
        return err_result("账户余额不足（HTTP 402）", status="ok")
    if resp.status_code != 200:
        return err_result(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return err_result("响应不是有效 JSON")

    infos = data.get("balance_infos") or []
    if not infos:
        return err_result("响应中无 balance_infos，请核对响应内容")

    windows = []
    for it in infos:
        cur = it.get("currency") or "CNY"
        windows.append({
            "level": "balance",
            "name": f"账户余额({cur})",
            "used_pct": None,
            "remaining_pct": None,
            "reset_ms": 0,
            "balance": it.get("total_balance") or "0.00",
            "granted": it.get("granted_balance") or "0.00",
            "topped_up": it.get("topped_up_balance") or "0.00",
        })
    note = None if data.get("is_available") else "⚠ 账户余额不足以调用 API"
    return ok_result(windows, note=note)

# ---------------------------------------------------------------- 主窗口引用（悬浮条共用）

_window_ref = {"win": None}      # pywebview 窗口引用（用于显示/隐藏）


def _show_window(*_a):
    """悬浮条菜单：显示/打开窗口"""
    win = _window_ref.get("win")
    if win is not None:
        try:
            win.show()
            win.restore()
            return
        except Exception:
            pass
    # Edge 降级模式或窗口已销毁：重新打开一个浏览器窗口
    threading.Thread(target=lambda: _reopen_window(), daemon=True).start()


def _reopen_window():
    import subprocess
    edge = _find_edge()
    url = f"http://{HOST}:{PORT}"
    if edge:
        subprocess.Popen([edge, f"--app={url}", "--no-first-run",
                          "--no-default-browser-check", "--window-size=1080,780"])
    else:
        webbrowser.open(url)


def _quit_app():
    """悬浮条/托盘菜单：彻底退出"""
    _tray_remove()
    os._exit(0)


def _create_shortcut(name: str = "CCMonitor"):
    """在 Windows 开始菜单创建快捷方式，方便用户搜索/固定到任务栏"""
    try:
        import winshell
        from win32com.client import Dispatch
    except ImportError:
        return False, "缺少 winshell/pywin32，无法创建快捷方式"
    try:
        start_menu = winshell.start_menu()
        exe = os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__)
        if not getattr(sys, "frozen", False):
            return False, "仅支持在打包后的 exe 中创建快捷方式"
        lnk = os.path.join(start_menu, "Programs", f"{name}.lnk")
        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(lnk)
        shortcut.Targetpath = exe
        shortcut.WorkingDirectory = os.path.dirname(exe)
        shortcut.Description = "CCMonitor · AI Coding Plan 余量监控"
        shortcut.save()
        return True, lnk
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- 任务栏悬浮条（Win32 原生叠加窗口）

_bar_hwnd = None
_bar_dragging = False    # 用户正在拖动（拖动中不重设位置）
_bar_screen_w = 0        # 主屏宽度（创建时缓存，悬浮条居中用）
_bar_work_bottom = 0     # 工作区底部 = 任务栏上沿（叠加位置用）
_gdiplus = None          # gdiplus.dll 扁平 API（懒加载）
_gdip_token = None

WM_APP_BAR_REFRESH = 0x8000 + 100   # 跨线程请求悬浮条重绘

# 悬浮条配色（与首页卡片规则一致：<70% 绿 · 70-89% 橙 · ≥90% 红）
_C_BG = (24, 26, 31)               # 深色底
_C_BORDER = (255, 255, 255)        # 描边（低透明度）
_C_NAME = (203, 213, 225)          # 供应商名（浅灰）
_C_GREEN = (52, 211, 153)          # 充裕
_C_ORANGE = (251, 191, 36)         # 偏高
_C_RED = (248, 113, 113)           # 接近耗尽
_C_BLUE = (96, 165, 250)           # 余额类
_C_MUTED = (148, 163, 184)         # 异常/无数据


class _GdipStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint),
                ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wintypes.BOOL),
                ("SuppressExternalCodecs", wintypes.BOOL)]


class _BlendFunc(ctypes.Structure):
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE),
                ("AlphaFormat", wintypes.BYTE)]


class _RectF(ctypes.Structure):
    _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float),
                ("Width", ctypes.c_float), ("Height", ctypes.c_float)]


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD)]


class _SizeL(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


WM_APP_BAR_REFRESH = 0x8000 + 100   # 跨线程请求悬浮条重绘
WM_APP_TRAY = 0x8000 + 200          # 托盘图标回调消息

_tray_nid = None                    # 当前托盘图标数据（None = 未添加）
_tray_balloon_shown = False


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _NID(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", _GUID), ("hBalloonIcon", wintypes.HICON)]


def _tray_icon_handle():
    ico = os.path.join(resource_dir(), "app.ico")
    if not os.path.exists(ico):
        ico = os.path.join(app_dir(), "app.ico")
    if os.path.exists(ico):
        return ctypes.windll.user32.LoadImageW(None, ico, 1, 16, 16, 0x10)
    return None


def _tray_tip_text() -> str:
    """托盘悬停提示：紧凑余量摘要"""
    lines = ["CCMonitor"]
    for seg in _bar_make_text():
        lines.append(f"{seg['name']} {seg['value']}".strip())
    return "\n".join(lines)[:127]


def _tray_sync(hwnd):
    """按 background 配置 添加/更新/移除 托盘图标（悬浮条线程内调用）"""
    global _tray_nid
    s32 = ctypes.windll.shell32
    if not CONFIG.get("background"):
        _tray_remove()
        return
    if _tray_nid:
        _tray_nid.szTip = _tray_tip_text()
        s32.Shell_NotifyIconW(1, byref(_tray_nid))       # NIM_MODIFY
        return
    nid = _NID()
    nid.cbSize = ctypes.sizeof(_NID)
    nid.hWnd = wintypes.HWND(hwnd)
    nid.uID = 1
    nid.uFlags = 0x1 | 0x2 | 0x4                         # MESSAGE | ICON | TIP
    nid.uCallbackMessage = WM_APP_TRAY
    hicon = _tray_icon_handle()
    if hicon:
        nid.hIcon = wintypes.HICON(hicon)
    nid.szTip = _tray_tip_text()
    if s32.Shell_NotifyIconW(0, byref(nid)):             # NIM_ADD
        _tray_nid = nid
        _diag(f"tray icon added hwnd={hwnd}")


def _tray_remove():
    global _tray_nid
    if _tray_nid:
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(2, byref(_tray_nid))
        except Exception:
            pass
        _tray_nid = None


def _tray_notify(title: str, msg: str):
    """托盘气泡提示"""
    if not _tray_nid:
        return
    nid = _NID()
    ctypes.memmove(byref(nid), byref(_tray_nid), ctypes.sizeof(_NID))
    nid.uFlags = 0x10                                    # NIF_INFO
    nid.szInfo = msg
    nid.szInfoTitle = title
    nid.dwInfoFlags = 0x1                                # NIIF_INFO
    ctypes.windll.shell32.Shell_NotifyIconW(1, byref(nid))


def _argb(rgb, a=255) -> int:
    """(r,g,b,a) → GDI+ ARGB DWORD"""
    r, g, b = rgb
    return (int(a) << 24) | (int(r) << 16) | (int(g) << 8) | int(b)


def _gdip_load():
    """初始化 GDI+（进程内一次性），返回扁平 API 或 None"""
    global _gdiplus, _gdip_token
    if _gdiplus is not None:
        return _gdiplus
    try:
        g = ctypes.WinDLL("gdiplus")
        token = ctypes.c_ulong(0)
        inp = _GdipStartupInput(1, None, False, False)
        if g.GdiplusStartup(byref(token), byref(inp), None) != 0:
            return None
        _gdip_token = token
        _gdiplus = g
    except Exception:
        return None
    P = ctypes.POINTER
    V = ctypes.c_void_p
    g.GdipCreateFontFamilyFromName.argtypes = [wintypes.LPCWSTR, V, P(V)]
    g.GdipCreateFont.argtypes = [V, ctypes.c_float, ctypes.c_int,
                                 ctypes.c_int, P(V)]
    g.GdipStringFormatGetGenericDefault.argtypes = [P(V)]
    g.GdipSetStringFormatLineAlign.argtypes = [V, ctypes.c_int]
    g.GdipCreateFromHDC.argtypes = [wintypes.HDC, P(V)]
    g.GdipMeasureString.argtypes = [V, wintypes.LPCWSTR, ctypes.c_int, V,
                                    P(_RectF), V, P(_RectF),
                                    P(ctypes.c_int), P(ctypes.c_int)]
    g.GdipDrawString.argtypes = [V, wintypes.LPCWSTR, ctypes.c_int, V,
                                 P(_RectF), V, V]
    g.GdipCreateSolidFill.argtypes = [ctypes.c_uint, P(V)]
    g.GdipCreatePen1.argtypes = [ctypes.c_uint, ctypes.c_float, ctypes.c_int,
                                 P(V)]
    g.GdipCreatePath.argtypes = [ctypes.c_int, P(V)]
    g.GdipAddPathArcI.argtypes = [V, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int,
                                  ctypes.c_float, ctypes.c_float]
    g.GdipClosePathFigure.argtypes = [V]
    g.GdipFillPath.argtypes = [V, V, V]
    g.GdipDrawPath.argtypes = [V, V, V]
    g.GdipFillEllipseI.argtypes = [V, V, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int]
    g.GdipSetSmoothingMode.argtypes = [V, ctypes.c_int]
    g.GdipSetTextRenderingHint.argtypes = [V, ctypes.c_int]
    for fn in ("GdipDeletePen", "GdipDeleteBrush", "GdipDeletePath",
               "GdipDeleteGraphics", "GdipDeleteFont",
               "GdipDeleteFontFamily", "GdipDeleteStringFormat"):
        getattr(g, fn).argtypes = [V]
    return _gdiplus


def _bar_status_color(used: float):
    """已用百分比 → 数值颜色"""
    if used >= 90:
        return _C_RED
    if used >= 70:
        return _C_ORANGE
    return _C_GREEN


def _bar_make_text():
    """生成悬浮条分段数据：每个启用供应商一段 {name, value, color}"""
    segs = []
    items = (CONFIG.get("bar") or {}).get("items") or list(CONFIG["providers"])
    for pid in items:
        p = CONFIG["providers"].get(pid)
        if not p or not p.get("enabled"):
            continue
        with _cache_lock:
            r = dict(CACHE.get(pid) or {})
        name = _short_name(p.get("name", pid))
        st = r.get("status")
        if st != "ok":
            if st == "disabled":
                continue
            text = {"expired": "凭据失效", "unverified": "端点待验证"}.get(
                st, (r.get("error") or "异常"))
            if len(text) > 14:
                text = text[:12] + "…"
            segs.append({"name": name, "value": text, "color": _C_MUTED})
            continue
        vals, worst = [], 0.0
        for w in r.get("windows") or []:
            if w.get("balance") is not None and w.get("used_pct") is None:
                vals.append(f"¥{w['balance']}")
            elif w.get("remaining_pct") is not None:
                used = 100.0 - float(w["remaining_pct"])
                worst = max(worst, used)
                vals.append(f"{w['remaining_pct']:.0f}%")
        if not vals:
            segs.append({"name": name, "value": "无数据", "color": _C_MUTED})
            continue
        color = _C_BLUE if all("¥" in v for v in vals) \
            else _bar_status_color(worst)
        segs.append({"name": name, "value": "/".join(vals), "color": color})
    return segs


def _short_name(name: str) -> str:
    """供应商显示名压缩为短名，避免悬浮条过长"""
    table = {"火山方舟 Coding Plan": "火山", "OpenCode Go": "OpenCode",
             "OpenAI Codex (ChatGPT Plan)": "Codex",
             "DeepSeek 开放平台": "DeepSeek"}
    return table.get(name, name.split()[0][:10] if name else "?")


def _bar_place(width: int):
    """计算悬浮条 x 坐标：未拖动过 → 任务栏上居中；拖动过 → 用保存坐标"""
    cfg = CONFIG.get("bar") or {}
    sx, sy = cfg.get("x"), cfg.get("y")
    if sx is not None and sy is not None:
        return int(sx), int(sy)
    return max(0, (_bar_screen_w - width) // 2), _bar_work_bottom


def _bar_rounded_path(g, x, y, w, h, r):
    """GDI+ 圆角矩形路径"""
    path = ctypes.c_void_p()
    g.GdipCreatePath(0, byref(path))            # FillModeAlternate
    d = 2 * r
    for (rx, ry, sa) in ((x, y, 180), (x + w - d, y, 270),
                         (x + w - d, y + h - d, 0), (x, y + h - d, 90)):
        g.GdipAddPathArcI(path, rx, ry, d, d, sa, 90)
    g.GdipClosePathFigure(path)
    return path


def _bar_render():
    """重绘悬浮条（GDI+ 圆角深色底 + 多供应商分段，UpdateLayeredWindow 上屏）"""
    hwnd = _bar_hwnd
    if not hwnd:
        return
    if not (CONFIG.get("bar") or {}).get("enabled", True):
        _tray_sync(hwnd)     # 悬浮条关闭时仅刷新托盘提示
        return
    g = _gdip_load()
    if not g:
        _diag("bar render: gdiplus unavailable")
        return
    try:
        _bar_render_impl(hwnd, g)
    except Exception:
        _diag("bar render error:\n" + traceback.format_exc())


def _bar_render_impl(hwnd, g):
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    cfg = CONFIG.get("bar") or {}
    fs = max(8, min(28, int(cfg.get("font_size") or 14)))
    opacity = max(30, min(100, int(cfg.get("opacity") or 92)))

    segs = _bar_make_text()
    if not segs:
        segs = [{"name": "", "value": "等待数据…", "color": _C_MUTED}]

    # ---- GDI+ 字体与格式
    fam = ctypes.c_void_p()
    g.GdipCreateFontFamilyFromName("Microsoft YaHei UI", None, byref(fam))
    if not fam.value:
        _diag("bar render: font family missing")
        return
    f_reg = ctypes.c_void_p()
    g.GdipCreateFont(fam, ctypes.c_float(fs), 0, 2, byref(f_reg))      # Regular / UnitPixel
    f_bold = ctypes.c_void_p()
    g.GdipCreateFont(fam, ctypes.c_float(fs), 1, 2, byref(f_bold))     # Bold
    fmt = ctypes.c_void_p()
    g.GdipStringFormatGetGenericDefault(byref(fmt))
    g.GdipSetStringFormatLineAlign(fmt, 1)     # 行内垂直居中

    # ---- 测量用临时 graphics
    scratch_dc = gdi32.CreateCompatibleDC(None)
    bmi = _BMI()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    bmi.bmiHeader.biWidth = 4
    bmi.bmiHeader.biHeight = -4
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    scratch_bmp = gdi32.CreateDIBSection(scratch_dc, byref(bmi), 0,
                                         byref(bits), None, 0)
    old_scratch = gdi32.SelectObject(scratch_dc, scratch_bmp)
    mgraph = ctypes.c_void_p()
    g.GdipCreateFromHDC(scratch_dc, byref(mgraph))

    PAD_X, PAD_Y, DOT = 16, 8, 8
    GAP1, GAP2, SEG_GAP, ROW_GAP = 7, 8, 22, 3

    def measure(text, font):
        rf = _RectF(0.0, 0.0, 100000.0, 1000.0)
        out = _RectF()
        g.GdipMeasureString(mgraph, text, -1, font, byref(rf), fmt,
                            byref(out), None, None)
        return out.Width, out.Height

    laid = []
    for s in segs:
        nw, nh = measure(s["name"], f_reg) if s["name"] else (0.0, 0.0)
        vw, vh = measure(s["value"], f_bold)
        sw = DOT + GAP1 + vw + (nw + GAP2 if s["name"] else 0)
        laid.append({"name": s["name"], "value": s["value"], "color": s["color"],
                     "nw": nw, "vw": vw, "sw": sw, "th": max(nh, vh)})

    # ---- 布局：贪心换行
    row_h = int(max(l["th"] for l in laid)) + 8
    max_w = max(300, _bar_screen_w - 40)
    avail = max_w - 2 * PAD_X
    rows, cur, cur_w = [], [], 0
    for l in laid:
        add = l["sw"] + (SEG_GAP if cur else 0)
        if cur and cur_w + add > avail:
            rows.append(cur)
            cur, cur_w = [l], l["sw"]
        else:
            cur.append(l)
            cur_w += add
    rows.append(cur)
    win_w = int(min(max_w, max(
        sum(l["sw"] for l in r) + SEG_GAP * (len(r) - 1) for r in rows)
        + 2 * PAD_X))
    win_h = int(2 * PAD_Y + len(rows) * row_h + (len(rows) - 1) * ROW_GAP)
    x, y = _bar_place(win_w)

    # ---- 目标位图：top-down 32bpp，零初始化（透明黑），GDI+ 叠色后天然满足预乘 alpha
    bmi2 = _BMI()
    bmi2.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    bmi2.bmiHeader.biWidth = win_w
    bmi2.bmiHeader.biHeight = -win_h
    bmi2.bmiHeader.biPlanes = 1
    bmi2.bmiHeader.biBitCount = 32
    bits2 = ctypes.c_void_p()
    dib = gdi32.CreateDIBSection(scratch_dc, byref(bmi2), 0,
                                 byref(bits2), None, 0)
    if dib:
        memdc = gdi32.CreateCompatibleDC(None)
        old_bmp = gdi32.SelectObject(memdc, dib)
        graph = ctypes.c_void_p()
        g.GdipCreateFromHDC(memdc, byref(graph))
        g.GdipSetSmoothingMode(graph, 4)        # SmoothingModeAntiAlias
        g.GdipSetTextRenderingHint(graph, 3)    # AntiAliasGridFit（兼容预乘 alpha）

        # 背景圆角矩形 + 细描边
        path = _bar_rounded_path(g, 0, 0, win_w, win_h,
                                 min(10, win_h // 2 - 1))
        bg_brush = ctypes.c_void_p()
        g.GdipCreateSolidFill(ctypes.c_uint(_argb(_C_BG, 255 * opacity // 100)),
                              byref(bg_brush))
        g.GdipFillPath(graph, bg_brush, path)
        pen = ctypes.c_void_p()
        g.GdipCreatePen1(ctypes.c_uint(_argb(_C_BORDER, 40)),
                         ctypes.c_float(1.0), 2, byref(pen))
        g.GdipDrawPath(graph, pen, path)
        g.GdipDeletePen(pen)
        g.GdipDeleteBrush(bg_brush)
        g.GdipDeletePath(path)

        # 分段：状态点 + 名称 + 数值
        for ri, row in enumerate(rows):
            y0 = PAD_Y + ri * (row_h + ROW_GAP)
            x0 = PAD_X
            for l in row:
                val_brush = ctypes.c_void_p()
                g.GdipCreateSolidFill(ctypes.c_uint(_argb(l["color"])),
                                      byref(val_brush))
                g.GdipFillEllipseI(graph, val_brush, int(x0),
                                   int(y0 + (row_h - DOT) // 2), DOT, DOT)
                x0 += DOT + GAP1
                if l["name"]:
                    name_brush = ctypes.c_void_p()
                    g.GdipCreateSolidFill(ctypes.c_uint(_argb(_C_NAME)),
                                          byref(name_brush))
                    rf = _RectF(float(x0), float(y0),
                                float(l["nw"] + 40), float(row_h))
                    g.GdipDrawString(graph, l["name"], -1, f_reg,
                                     byref(rf), fmt, name_brush)
                    g.GdipDeleteBrush(name_brush)
                    x0 += l["nw"] + GAP2
                rf2 = _RectF(float(x0), float(y0),
                             float(l["vw"] + 40), float(row_h))
                g.GdipDrawString(graph, l["value"], -1, f_bold,
                                 byref(rf2), fmt, val_brush)
                g.GdipDeleteBrush(val_brush)
                x0 += l["vw"] + SEG_GAP
        g.GdipDeleteGraphics(graph)

        # ---- 上屏：UpdateLayeredWindow（拖动中不改变窗口位置）
        pos = wintypes.POINT(x, y)
        size = _SizeL(win_w, win_h)
        src = wintypes.POINT(0, 0)
        blend = _BlendFunc(0, 0, 255, 1)   # AC_SRC_OVER / alpha 255 / AC_SRC_ALPHA
        hdc_screen = user32.GetDC(None)
        user32.UpdateLayeredWindow(hwnd, hdc_screen,
                                   None if _bar_dragging else byref(pos),
                                   byref(size), memdc, byref(src), 0,
                                   byref(blend), 2)
        user32.ReleaseDC(None, hdc_screen)
        gdi32.SelectObject(memdc, old_bmp)
        gdi32.DeleteObject(dib)
        gdi32.DeleteDC(memdc)
    gdi32.SelectObject(scratch_dc, old_scratch)
    gdi32.DeleteObject(scratch_bmp)
    gdi32.DeleteDC(scratch_dc)
    g.GdipDeleteFont(f_reg)
    g.GdipDeleteFont(f_bold)
    g.GdipDeleteFontFamily(fam)
    g.GdipDeleteStringFormat(fmt)


def _bar_request_refresh():
    """线程安全地请求悬浮条重绘（PostMessage 转交悬浮条线程执行）"""
    if _bar_hwnd:
        ctypes.windll.user32.PostMessageW(
            wintypes.HWND(_bar_hwnd), WM_APP_BAR_REFRESH, 0, 0)


def start_bar():
    """创建叠加在任务栏上的置顶悬浮条（daemon 线程 + Win32 原生窗口）"""
    global _bar_hwnd
    cfg = CONFIG.get("bar") or {}
    if not cfg.get("enabled"):
        return None
    try:
        t = threading.Thread(target=_bar_thread, daemon=True)
        t.start()
        _diag("bar thread started")
        return t
    except Exception as e:
        _diag(f"bar failed: {e}")
        return None


def _bar_thread():
    """悬浮条窗口线程：注册窗口类 → 创建窗口 → 消息循环"""
    global _bar_hwnd
    try:
        import ctypes
        from ctypes import wintypes, WINFUNCTYPE, Structure, byref

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        WM_PAINT = 0x000F
        WM_TIMER = 0x0113
        WM_DESTROY = 0x0002
        WM_LBUTTONDOWN = 0x0201
        WM_LBUTTONDBLCLK = 0x0203
        WM_RBUTTONDOWN = 0x0204
        WM_NCHITTEST = 0x0084
        WM_COMMAND = 0x0111
        WM_ERASEBKGND = 0x0014
        WM_EXITSIZEMOVE = 0x0232
        WM_ENTERSIZEMOVE = 0x0231
        WM_DISPLAYCHANGE = 0x007E
        WS_POPUP = 0x80000000
        WS_VISIBLE = 0x10000000
        WS_EX_TOPMOST = 0x00000008
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_LAYERED = 0x00080000
        WS_EX_NOACTIVATE = 0x08000000
        HTCAPTION = 2
        MENU_OPEN, MENU_REFRESH, MENU_QUIT = 1, 2, 3

        LRESULT = ctypes.c_longlong
        user32.DefWindowProcW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = LRESULT
        WNDPROC = WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEX(Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HICON),
                        ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR),
                        ("hIconSm", wintypes.HICON)]

        class MSG(Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt", wintypes.POINT)]

        def _popup_app_menu():
            """悬浮条/托盘共用右键菜单"""
            hmenu = user32.CreatePopupMenu()
            user32.AppendMenuW(hmenu, 0, MENU_OPEN, "打开窗口")
            user32.AppendMenuW(hmenu, 0, MENU_REFRESH, "立即刷新")
            user32.AppendMenuW(hmenu, 0x00000800, 0, None)  # SEPARATOR
            user32.AppendMenuW(hmenu, 0, MENU_QUIT, "退出")
            pt = wintypes.POINT()
            user32.GetCursorPos(byref(pt))
            user32.SetForegroundWindow(hwnd)
            user32.TrackPopupMenu(
                hmenu, 0x0002 | 0x0100, pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, 0, 0, 0)
            user32.DestroyMenu(hmenu)

        def wnd_proc(hwnd, msg, wparam, lparam):
            global _bar_dragging
            try:
                if msg in (WM_PAINT, WM_ERASEBKGND):
                    return 0     # 内容由 UpdateLayeredWindow 全权管理
                elif msg == WM_TIMER and wparam == 2:
                    # 周期性重新置顶：任务栏被点击激活后 z 序会盖住悬浮条
                    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0,
                                        0x0001 | 0x0002 | 0x0010)  # TOPMOST|NOSIZE|NOMOVE|NOACTIVATE
                    return 0
                elif msg == WM_APP_TRAY:
                    ev = lparam & 0xFFFF
                    if ev == 0x0202:          # WM_LBUTTONUP → 恢复窗口
                        threading.Thread(target=_show_window,
                                         daemon=True).start()
                    elif ev == 0x0205:        # WM_RBUTTONUP → 菜单
                        _popup_app_menu()
                    return 0
                elif msg == WM_TIMER and not bar_active:
                    return 0
                elif msg == WM_TIMER or msg == WM_APP_BAR_REFRESH \
                        or msg == WM_DISPLAYCHANGE:
                    _bar_render()
                    return 0
                elif msg in (WM_LBUTTONDOWN, WM_LBUTTONDBLCLK):
                    threading.Thread(target=_show_window, daemon=True).start()
                    return 0
                elif msg == WM_RBUTTONDOWN:
                    _popup_app_menu()
                    return 0
                elif msg == WM_COMMAND:
                    wid = wparam & 0xFFFF
                    if wid == MENU_OPEN:
                        threading.Thread(target=_show_window, daemon=True).start()
                    elif wid == MENU_REFRESH:
                        threading.Thread(
                            target=lambda: (query_all(), _bar_request_refresh()),
                            daemon=True).start()
                    elif wid == MENU_QUIT:
                        user32.DestroyWindow(hwnd)
                        _quit_app()
                    return 0
                elif msg == WM_NCHITTEST:
                    return HTCAPTION      # 允许拖动悬浮条
                elif msg == WM_ENTERSIZEMOVE:
                    _bar_dragging = True
                    return 0
                elif msg == WM_EXITSIZEMOVE:
                    # 用户拖动结束 → 保存当前位置（仅用户拖动触发，创建/自动调整不保存）
                    _bar_dragging = False
                    try:
                        rect = wintypes.RECT()
                        user32.GetWindowRect(hwnd, byref(rect))
                        cfg = CONFIG.get("bar") or {}
                        cfg["x"] = rect.left
                        cfg["y"] = rect.top
                        save_config(CONFIG)
                        _diag(f"bar position saved: ({rect.left},{rect.top})")
                    except Exception:
                        pass
                    return 0
                elif msg == WM_DESTROY:
                    _tray_remove()
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as e:
                _diag(f"bar wndproc error: {e}")
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc_ref = WNDPROC(wnd_proc)      # 保持引用，防止被 GC
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "CCMonitorBarClass"
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = 0x0002 | 0x0001        # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = proc_ref
        wc.hInstance = hinstance
        wc.hCursor = user32.LoadCursorW(None, 32649)   # IDC_HAND
        wc.hbrBackground = None     # 内容由 UpdateLayeredWindow 管理，无需背景刷
        wc.lpszClassName = class_name
        if not user32.RegisterClassExW(byref(wc)):
            _diag("bar: RegisterClassEx failed (may already exist)")

        # 初始位置：叠加在任务栏上居中（首刷后按实测文本宽度自适应）
        bar_active = bool((CONFIG.get("bar") or {}).get("enabled", True))
        class RECT_T(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        wa = RECT_T()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)  # work area
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        global _bar_screen_w, _bar_work_bottom
        _bar_screen_w = sw
        _bar_work_bottom = wa.bottom   # 任务栏上沿 = 悬浮条默认叠加位置
        x, y = _bar_place(320)         # 初始占位尺寸，首次渲染即按实际内容重设
        _diag(f"bar geometry: screen={sw}x{sh} workarea_bottom={wa.bottom} "
              f"pos=({x},{y}) active={bar_active}")

        # bar 关闭时窗口仅作托盘消息宿主，不显示
        style = WS_POPUP | (WS_VISIBLE if bar_active else 0)
        hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE,
            class_name, "CCMonitor Bar", style,
            x, y, 320, 34,
            None, None, hinstance, None)
        if not hwnd:
            _diag(f"bar: CreateWindowEx failed, err={ctypes.get_last_error()}")
            return
        _bar_hwnd = hwnd

        if bar_active:
            interval = int((CONFIG.get("bar") or {}).get("refresh_seconds") or 30) * 1000
            user32.SetTimer(hwnd, 1, interval, None)
            user32.SetTimer(hwnd, 2, 2000, None)     # 置顶保持
            _gdip_load()
            _bar_render()
        _tray_sync(hwnd)                 # 托盘图标（最小化开关开启时添加）
        _diag(f"bar created hwnd={hwnd} active={bar_active}")

        # 消息循环
        msg = MSG()
        while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))
    except Exception as e:
        _diag(f"bar thread exception: {e}")


# ---------------------------------------------------------------- 查询调度

PROVIDER_QUERY = {
    "volcengine": query_volcengine,
    "opencode": query_opencode,
    "codex": query_codex,
    "deepseek": query_deepseek,
}

_cache_lock = threading.Lock()
CACHE = {}          # pid -> result dict（附 last_update_ms）
_refresh_timer = None


def query_one(pid: str) -> dict:
    p = CONFIG["providers"].get(pid)
    if not p:
        return err_result("未知供应商")
    if not p.get("enabled"):
        return {"status": "disabled", "windows": [], "note": "", "error": ""}
    try:
        result = PROVIDER_QUERY[pid](p)
    except Exception:
        result = err_result("内部异常：" + traceback.format_exc(limit=1))
    result["provider"] = pid
    result["display_name"] = p.get("name", pid)
    with _cache_lock:
        CACHE[pid] = result
        CACHE[pid]["last_update_ms"] = int(time.time() * 1000)
    return result


def query_all() -> dict:
    return {pid: query_one(pid) for pid in CONFIG["providers"]}


def _sync_disabled_cache():
    """将禁用供应商的缓存标记为 disabled，并清除旧数据，保证首页实时反映启用状态"""
    with _cache_lock:
        for pid, p in CONFIG["providers"].items():
            if not p.get("enabled"):
                old = CACHE.get(pid, {})
                old.clear()
                old.update({"status": "disabled", "windows": [],
                            "note": "", "error": "",
                            "provider": pid,
                            "display_name": p.get("name", pid),
                            "last_update_ms": int(time.time() * 1000)})
                CACHE[pid] = old


def _auto_refresh_loop():
    """自动刷新线程：按配置间隔轮询全部启用供应商"""
    while True:
        interval = float(CONFIG.get("refresh_minutes") or 0) * 60
        if interval <= 0:
            time.sleep(15)   # 关闭状态下低频空转，等待配置变更
            continue
        query_all()
        _bar_request_refresh()  # 同步刷新悬浮条
        time.sleep(interval)

# ---------------------------------------------------------------- Flask 应用

app = Flask(APP_NAME, static_folder=None)
UI_DIR = os.path.join(resource_dir(), "ui")


@app.get("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(UI_DIR, filename)


@app.get("/api/usage")
def api_usage():
    force = request.args.get("force") == "1"
    if force:
        query_all()
    else:
        _sync_disabled_cache()    # 确保禁用状态实时反映到缓存
    with _cache_lock:
        return jsonify({"config": {
            "refresh_minutes": CONFIG.get("refresh_minutes"),
            "card_order": CONFIG.get("card_order", [])},
            "results": CACHE})


@app.get("/api/settings")
def api_settings_get():
    return jsonify(CONFIG)


@app.post("/api/settings")
def api_settings_set():
    global _refresh_timer
    body = request.get_json(silent=True) or {}
    try:
        rm = body.get("refresh_minutes")
        if rm is not None:
            CONFIG["refresh_minutes"] = max(0, int(rm))
        bg = body.get("background")
        if isinstance(bg, bool):
            CONFIG["background"] = bg
        px = body.get("proxy")
        if isinstance(px, str):
            CONFIG["proxy"] = px.strip()
        co = body.get("card_order")
        if isinstance(co, list):
            CONFIG["card_order"] = [x for x in co if x in CONFIG["providers"]]
            # 补全新增的供应商到末尾
            for pid in CONFIG["providers"]:
                if pid not in CONFIG["card_order"]:
                    CONFIG["card_order"].append(pid)
        br = body.get("bar")
        if isinstance(br, dict):
            CONFIG.setdefault("bar", {}).update(br)
            # 过滤无效供应商 id
            CONFIG["bar"]["items"] = [
                x for x in (CONFIG["bar"].get("items") or [])
                if x in CONFIG["providers"]]
        for pid, p in (body.get("providers") or {}).items():
            if pid in CONFIG["providers"]:
                CONFIG["providers"][pid].update(p)
        save_config(CONFIG)
        _sync_disabled_cache()   # 设置保存后立即同步缓存状态
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/test/<pid>")
def api_test(pid):
    if pid not in PROVIDER_QUERY:
        return jsonify(err_result("未知供应商")), 404
    return jsonify(query_one(pid))


_MUTEX = "Local\\CCMonitor_SingleInstance"
_mutex_handle = None


def _single_instance_acquire() -> bool:
    """尝试获取单实例互斥体。返回 False 表示已有实例在运行。"""
    global _mutex_handle
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = wintypes.HANDLE
    _mutex_handle = k32.CreateMutexW(None, wintypes.BOOL(False), _MUTEX)
    return ctypes.GetLastError() != 183    # ERROR_ALREADY_EXISTS


def _signal_existing_instance():
    """唤回已运行实例的主窗口；窗口不存在（Edge 模式关窗）时模拟点击其悬浮条重开"""
    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, "CCMonitor · AI Coding Plan 余量监控")
    if hwnd:
        u32.ShowWindow(wintypes.HWND(hwnd), 9)      # SW_RESTORE
        u32.ShowWindow(wintypes.HWND(hwnd), 5)      # SW_SHOW
        u32.SetForegroundWindow(wintypes.HWND(hwnd))
        return
    bar = u32.FindWindowW("CCMonitorBarClass", None)
    if bar:
        u32.PostMessageW(wintypes.HWND(bar), 0x0201, 1, (10 << 16) | 10)


def _open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


def _find_edge() -> str:
    """定位 Edge 浏览器（Windows 10/11 必带）"""
    import shutil
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.exists(p):
            return p
    found = shutil.which("msedge")
    return found or ""


def _diag(msg: str):
    """窗口模式诊断日志（写到 exe 同目录，排查窗口问题用）"""
    try:
        with open(os.path.join(app_dir(), "window_debug.log"), "a",
                  encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def _apply_window_icon():
    """轮询主窗口出现后，用 WM_SETICON 设置任务栏/标题栏图标（pywebview 不支持自定义图标）"""
    u32 = ctypes.windll.user32
    hwnd = None
    for _ in range(100):        # 最多等 20 秒
        hwnd = u32.FindWindowW(None, "CCMonitor · AI Coding Plan 余量监控")
        if hwnd:
            break
        time.sleep(0.2)
    if not hwnd:
        return
    ico = os.path.join(resource_dir(), "app.ico")
    if not os.path.exists(ico):
        ico = os.path.join(app_dir(), "app.ico")
    if not os.path.exists(ico):
        return
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
    h_big = u32.LoadImageW(None, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
    h_small = u32.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
    if h_big:
        u32.SendMessageW(wintypes.HWND(hwnd), 0x0080, 1, wintypes.HANDLE(h_big))
    if h_small:
        u32.SendMessageW(wintypes.HWND(hwnd), 0x0080, 0, wintypes.HANDLE(h_small))
    _diag(f"window icon applied hwnd={hwnd}")


def _open_app_window() -> None:
    """应用窗口模式：pywebview 原生窗口 → Edge --app 独立窗口 → 浏览器兜底"""
    url = f"http://{HOST}:{PORT}"
    _diag(f"start _open_app_window, url={url}")

    # 方式一：pywebview 原生窗口（主线程阻塞，watchdog 超时后 destroy 释放）
    try:
        import webview
        _diag("pywebview imported, creating window")
        win = webview.create_window(
            "CCMonitor · AI Coding Plan 余量监控", url,
            width=1080, height=780, min_size=(860, 600))
        _window_ref["win"] = win      # 供悬浮条点击时恢复窗口
        threading.Thread(target=_apply_window_icon, daemon=True).start()
        loaded = threading.Event()

        def _on_loaded(*_a):
            loaded.set()
        win.events.loaded += _on_loaded

        def watchdog():
            # 8 秒内未加载完成 → 强制 destroy 让 webview.start 返回
            if not loaded.wait(8):
                _diag("watchdog: 8s timeout, destroying window")
                try:
                    win.destroy()
                except Exception as e:
                    _diag(f"watchdog: destroy failed: {e}")

        threading.Thread(target=watchdog, daemon=True).start()

        # 关闭拦截：开启后台驻留时，关闭窗口 = 隐藏到托盘（任务栏按钮消失，悬浮条保持）
        def _on_closing():
            global _tray_balloon_shown
            if CONFIG.get("background"):
                _diag("window closing -> hide to tray (background)")
                try:
                    win.hide()
                except Exception:
                    pass
                if not _tray_balloon_shown:
                    _tray_balloon_shown = True
                    _tray_notify("CCMonitor 已隐藏到托盘",
                                 "点击托盘图标或悬浮条可恢复窗口；"
                                 "托盘/悬浮条右键 → 退出")
                return False    # 取消关闭，应用继续后台运行
            _tray_remove()      # 未开启后台驻留 → 正常退出
            return True
        try:
            win.events.closing += _on_closing
            _diag("closing handler registered")
        except Exception as e:
            _diag(f"closing handler failed: {e}")

        _diag("calling webview.start(edgechromium) on main thread")
        webview.start(gui="edgechromium")
        _diag(f"webview.start returned, loaded={loaded.is_set()}")
        if loaded.is_set():
            _diag("window loaded and closed normally, exiting")
            return
        _diag("webview loaded=False, falling through to Edge")
    except Exception as e:
        _diag(f"pywebview setup/start exception: {e}")

    # 方式二：Edge --app 独立窗口
    edge = _find_edge()
    _diag(f"edge path: {edge!r}")
    if edge:
        import subprocess
        profile = os.path.join(
            os.environ.get("TEMP", os.getcwd()),
            f"CCMonitor_WebView_{os.getpid()}")
        cmd = [edge, f"--app={url}", f"--user-data-dir={profile}",
               "--no-first-run", "--no-default-browser-check",
               "--window-size=1080,780"]
        _diag(f"launching edge: {cmd}")
        proc = subprocess.Popen(cmd)
        rc = proc.wait()
        _diag(f"edge exited, returncode={rc}")
        if CONFIG.get("background"):
            # 后台驻留：悬浮条/本地服务继续运行，点击悬浮条可重开窗口
            _diag("edge window closed -> staying in background")
            while True:
                time.sleep(3600)
        _tray_remove()
        os._exit(0)
    else:
        _diag("no edge found, falling back to default browser")
        _open_browser()
        if CONFIG.get("background"):
            while True:
                time.sleep(3600)


def main():
    if not _single_instance_acquire():
        # 已有实例在运行：唤回其主窗口，本进程退出（悬浮条不会重复创建）
        _signal_existing_instance()
        os._exit(0)
    os.makedirs(UI_DIR, exist_ok=True)
    threading.Thread(target=_auto_refresh_loop, daemon=True).start()
    threading.Thread(
        target=lambda: app.run(host=HOST, port=PORT, debug=False,
                               use_reloader=False),
        daemon=True).start()
    start_bar()              # 启动任务栏悬浮条（叠加显示余量）
    _open_app_window()


if __name__ == "__main__":
    main()
