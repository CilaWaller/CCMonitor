/* CCMonitor 前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);
let LAST_DATA = null;

/* ---------- 视图切换 ---------- */
$("tab-home").onclick = () => switchTab("home");
$("tab-settings").onclick = () => switchTab("settings");
function switchTab(v) {
  $("view-home").classList.toggle("hidden", v !== "home");
  $("view-settings").classList.toggle("hidden", v !== "settings");
  $("tab-home").classList.toggle("active", v === "home");
  $("tab-settings").classList.toggle("active", v === "settings");
  if (v === "settings") loadSettings();
}

/* ---------- 工具 ---------- */
function colorClass(used) {
  if (used >= 90) return "c-red";
  if (used >= 70) return "c-orange";
  return "c-green";
}
function badgeOf(status) {
  return {
    ok: ["ok", "正常"], disabled: ["disabled", "已停用"],
    expired: ["expired", "会话已过期"], unverified: ["unverified", "端点待验证"],
    error: ["err", "异常"],
  }[status] || ["err", status];
}
function fmtCountdown(resetMs) {
  if (!resetMs) return "";
  const diff = resetMs - Date.now();
  if (diff <= 0) return "已到刷新时间";
  const m = Math.floor(diff / 60000), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d > 0) return `${d}天${h % 24}小时后刷新`;
  if (h > 0) return `${h}小时${m % 60}分钟后刷新`;
  return `${Math.max(1, m)}分钟后刷新`;
}

/* ---------- 余量渲染 ---------- */
async function loadUsage(force) {
  try {
    const resp = await fetch("/api/usage" + (force ? "?force=1" : ""));
    LAST_DATA = await resp.json();
    renderCards();
    $("last-update").textContent = "自动刷新间隔: " +
      ((LAST_DATA.config.refresh_minutes || 0) === 0
        ? "关闭" : LAST_DATA.config.refresh_minutes + " 分钟");
  } catch (e) {
    $("last-update").textContent = "加载失败：" + e;
  }
}

function renderCards() {
  const box = $("cards");
  box.innerHTML = "";
  const results = LAST_DATA.results || {};
  const order = LAST_DATA.config.card_order || [];
  // 按 card_order 排序；未在 order 中的按字典序追加
  const pids = Object.keys(results).sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib) || a.localeCompare(b);
  });
  for (const pid of pids) {
    const r = results[pid];
    if (r.status === "disabled") continue;   // 未启用的供应商不显示卡片
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.pid = pid;
    card.draggable = true;   // 支持拖拽排序

    const [bc, bt] = badgeOf(r.status);
    let html = `
      <div class="card-head">
        <span class="card-title">${esc(r.display_name || pid)}</span>
        <span class="badge ${bc}">${bt}</span>
      </div>`;
    if (r.note) html += `<div class="card-note">ℹ ${esc(r.note)}</div>`;
    if (r.error) html += `<div class="card-error">✕ ${esc(r.error)}</div>`;

    for (const w of r.windows || []) {
      if (w.balance !== undefined && w.used_pct === null) {
        // DeepSeek 等余额型窗口：显示金额而非进度条
        html += `
          <div class="win">
            <div class="win-head">
              <span class="win-name">${esc(w.name)}</span>
              <span class="win-remaining balance-amount">${esc(w.balance)}</span>
            </div>
            <div class="win-reset">赠金 ${esc(w.granted)} · 充值 ${esc(w.topped_up)}</div>
          </div>`;
        continue;
      }
      const used = w.used_pct ?? 0;
      const modelTag = w.model ? `<span class="win-model"> · ${esc(w.model)}</span>` : "";
      html += `
        <div class="win">
          <div class="win-head">
            <span class="win-name">${esc(w.name)}${modelTag}</span>
            <span class="win-remaining">剩余 ${w.remaining_pct}%</span>
          </div>
          <div class="bar"><div class="bar-fill ${colorClass(used)}"
               style="width:${used}%"></div></div>
          <div class="win-reset" data-reset="${w.reset_ms || 0}">已用 ${used}%</div>
        </div>`;
    }
    if (r.status === "ok" && !(r.windows || []).length) {
      html += `<div class="card-error">无窗口数据</div>`;
    }
    if (r.last_update_ms) {
      html += `<div class="card-time">更新于 ${new Date(r.last_update_ms).toLocaleTimeString()}</div>`;
    }
    card.innerHTML = html;
    box.appendChild(card);
  }
  tickCountdowns();
  bindDragSort();
}

/* ---------- 拖拽排序 ---------- */
let dragSrc = null;
function bindDragSort() {
  const box = $("cards");
  box.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      dragSrc = card;
      card.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", card.dataset.pid);
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      saveCardOrder();
    });
    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const tgt = card;
      if (dragSrc && tgt !== dragSrc) {
        const rect = tgt.getBoundingClientRect();
        const after = (e.clientX - rect.left) > rect.width / 2;
        if (after) tgt.parentNode.insertBefore(dragSrc, tgt.nextSibling);
        else tgt.parentNode.insertBefore(dragSrc, tgt);
      }
    });
    card.addEventListener("drop", (e) => e.preventDefault());
  });
}

async function saveCardOrder() {
  const pids = [...document.querySelectorAll("#cards .card")]
    .map((c) => c.dataset.pid);
  if (pids.length < 2) return;
  // 与当前 order 比较，有变化才保存
  const cur = (LAST_DATA.config.card_order || []).join(",");
  if (pids.join(",") === cur) return;
  LAST_DATA.config.card_order = pids;
  await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_order: pids }),
  });
}

/* 每秒刷新倒计时 */
function tickCountdowns() {
  document.querySelectorAll(".win-reset[data-reset]").forEach((el) => {
    const ms = Number(el.dataset.reset);
    if (!ms) { el.textContent = el.textContent.split("|")[0]; return; }
    const base = el.textContent.split("|")[0];
    el.textContent = `${base} | ${fmtCountdown(ms)}`;
  });
}
setInterval(tickCountdowns, 1000);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- 刷新按钮 ---------- */
$("btn-refresh").onclick = () => {
  $("btn-refresh").disabled = true;
  loadUsage(true).finally(() => { $("btn-refresh").disabled = false; });
};

/* ---------- 设置页 ---------- */
const PROVIDER_FIELDS = {
  volcengine: ["name", "ak", "sk", "project"],
  opencode: ["name", "api_key"],
  codex: ["name", "auth_path", "account_id"],
  deepseek: ["name", "api_key"],
};

async function loadSettings() {
  const cfg = await (await fetch("/api/settings")).json();
  LAST_CONFIG = cfg;
  $("set-refresh").value = cfg.refresh_minutes ?? 5;
  $("opt-background").checked = cfg.background ?? true;
  $("set-proxy").value = cfg.proxy ?? "";
  // 悬浮条设置
  const bar = cfg.bar || {};
  $("bar-enabled").checked = !!bar.enabled;
  $("bar-opacity").value = bar.opacity ?? 92;
  $("bar-refresh_seconds").value = bar.refresh_seconds ?? 30;
  $("bar-font_size").value = bar.font_size ?? 14;
  const items = bar.items || [];
  document.querySelectorAll(".tray-item-toggle").forEach((cb) => {
    cb.checked = items.includes(cb.dataset.pid);
  });
  for (const [pid, fields] of Object.entries(PROVIDER_FIELDS)) {
    const p = cfg.providers[pid] || {};
    $(`pv-${pid}-enabled`).checked = !!p.enabled;
    for (const f of fields) $(`pv-${pid}-${f}`).value = p[f] ?? "";
  }
}

let LAST_CONFIG = null;   // 最近一次加载的配置（供保存时保留未表单字段）

/* 收集表单 → 请求体（保存与测试前保存共用） */
function collectSettings() {
  const body = {
    refresh_minutes: Number($("set-refresh").value) || 0,
    background: $("opt-background").checked,
    proxy: $("set-proxy").value.trim(),
    providers: {},
  };
  const bar = (LAST_CONFIG && LAST_CONFIG.bar) || {};
  body.bar = {
    enabled: $("bar-enabled").checked,
    opacity: Number($("bar-opacity").value) || 92,
    refresh_seconds: Number($("bar-refresh_seconds").value) || 30,
    font_size: Number($("bar-font_size").value) || 14,
    x: bar.x,
    y: bar.y,
    items: [...document.querySelectorAll(".tray-item-toggle")]
      .filter((cb) => cb.checked).map((cb) => cb.dataset.pid),
  };
  for (const [pid, fields] of Object.entries(PROVIDER_FIELDS)) {
    body.providers[pid] = { enabled: $(`pv-${pid}-enabled`).checked };
    for (const f of fields) body.providers[pid][f] = $(`pv-${pid}-${f}`).value.trim();
  }
  return body;
}

$("btn-save").onclick = async () => {
  const body = collectSettings();
  const resp = await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const out = await resp.json();
  $("save-msg").textContent = out.ok ? "✓ 已保存" : "✕ " + out.error;
  $("save-msg").className = "test-result " + (out.ok ? "ok" : "bad");
  setTimeout(() => { $("save-msg").textContent = ""; }, 3000);
};

/* 悬浮条位置重置：清除保存的坐标，下次启动回到任务栏上居中 */
$("btn-bar-reset").onclick = async () => {
  if (LAST_CONFIG && LAST_CONFIG.bar) {
    LAST_CONFIG.bar.x = null;
    LAST_CONFIG.bar.y = null;
  }
  await saveQuietly();
  $("save-msg").textContent = "✓ 位置已重置，重启程序后生效";
  $("save-msg").className = "test-result ok";
  setTimeout(() => { $("save-msg").textContent = ""; }, 3000);
};

/* 测试连接 */
document.querySelectorAll(".btn.test").forEach((btn) => {
  btn.onclick = async () => {
    const pid = btn.dataset.pid;
    const tr = $("tr-" + pid);
    btn.disabled = true;
    tr.textContent = "测试中…"; tr.className = "test-result";
    try {
      await saveQuietly();           // 先保存再测试，保证用最新凭据
      const r = await (await fetch(`/api/test/${pid}`, { method: "POST" })).json();
      if (r.status === "ok") {
        tr.textContent = `✓ 成功：${(r.windows || []).length} 个窗口`;
        tr.className = "test-result ok";
      } else {
        tr.textContent = "✕ " + (r.error || r.status);
        tr.className = "test-result bad";
      }
    } catch (e) {
      tr.textContent = "✕ " + e; tr.className = "test-result bad";
    } finally {
      btn.disabled = false;
      setTimeout(() => { tr.textContent = ""; }, 6000);
    }
  };
});

async function saveQuietly() {
  await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collectSettings()),
  });
}

/* 启动 */
loadUsage();
setInterval(() => loadUsage(false), 30000);   // 前端每 30s 拉一次缓存
