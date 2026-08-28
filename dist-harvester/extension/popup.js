// Popup logic: detect current tab's engine → harvest cookies+localStorage → upload.

// engine_key → (匹配当前 tab URL host 的关键字, cookie 域列表)
// 一个 engine 可能跨多个子域(豆包有 doubao.com 主域 + lf-flow-web-cdn 等 CDN,
// 我们只抓主鉴权域,辅助域的 cookie 大多是埋点 / 防爬 token,丢了不影响登录)。
const ENGINES = {
  doubao: {
    name: "豆包",
    hostKeywords: ["doubao.com"],
    cookieDomains: [".doubao.com"],
    primaryOrigin: "https://www.doubao.com",
  },
  qwen: {
    name: "通义千问",
    hostKeywords: ["qianwen.com"],
    cookieDomains: [".qianwen.com", "www.qianwen.com"],
    primaryOrigin: "https://www.qianwen.com",
  },
  deepseek: {
    name: "DeepSeek",
    hostKeywords: ["deepseek.com"],
    cookieDomains: [".deepseek.com", "chat.deepseek.com"],
    primaryOrigin: "https://chat.deepseek.com",
  },
  wenxin: {
    name: "文心一言",
    hostKeywords: ["yiyan.baidu.com"],
    cookieDomains: [".baidu.com", "yiyan.baidu.com"],
    primaryOrigin: "https://yiyan.baidu.com",
  },
  yuanbao: {
    name: "腾讯元宝",
    hostKeywords: ["yuanbao.tencent.com"],
    cookieDomains: [".tencent.com", "yuanbao.tencent.com"],
    primaryOrigin: "https://yuanbao.tencent.com",
  },
};

// ── Helpers ──────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function setStatus(kind, msg) {
  const el = $("status");
  el.className = "status " + kind;
  el.textContent = msg;
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function detectEngine(url) {
  if (!url) return null;
  let hostname;
  try { hostname = new URL(url).hostname; } catch (e) { return null; }
  for (const [key, cfg] of Object.entries(ENGINES)) {
    if (cfg.hostKeywords.some(kw => hostname.includes(kw))) {
      return { key, ...cfg };
    }
  }
  return null;
}

async function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["api", "token", "label"], (data) => {
      resolve(data || {});
    });
  });
}

// Chrome cookies API → Playwright storage_state.cookies 格式映射
function chromeCookieToPlaywright(c) {
  // sameSite: Chrome "no_restriction"/"lax"/"strict"/"unspecified" → Playwright "None"/"Lax"/"Strict"
  let sameSite;
  switch ((c.sameSite || "").toLowerCase()) {
    case "no_restriction": sameSite = "None"; break;
    case "lax":            sameSite = "Lax"; break;
    case "strict":         sameSite = "Strict"; break;
    default:               sameSite = "Lax"; // Playwright 不接受 unspecified,统一兜底 Lax
  }
  const out = {
    name: c.name,
    value: c.value,
    domain: c.domain,
    path: c.path || "/",
    httpOnly: !!c.httpOnly,
    secure: !!c.secure,
    sameSite,
  };
  if (typeof c.expirationDate === "number") {
    out.expires = c.expirationDate;
  } else {
    out.expires = -1;  // session cookie
  }
  return out;
}

// 收集 cookies — chrome.cookies.getAll(per-domain),合并去重
async function collectCookies(domains) {
  const seen = new Set();
  const out = [];
  for (const domain of domains) {
    const cookies = await new Promise((resolve) => {
      chrome.cookies.getAll({ domain }, resolve);
    });
    for (const c of cookies) {
      const key = `${c.domain}|${c.path}|${c.name}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(chromeCookieToPlaywright(c));
    }
  }
  return out;
}

// 在 tab 里跑一段 JS 读 localStorage + sessionStorage(只读 localStorage,
// Playwright storage_state 不含 sessionStorage)。
// 顺手抓账号昵称(handle):页面上常见的掩码手机号(138****1234)——
// 纯展示用,抓不到/抓错都不影响上传和账号识别(识别靠服务端解 storage_state)。
async function readLocalStorage(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId, allFrames: false },
    func: () => {
      const items = [];
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          items.push({ name: k, value: localStorage.getItem(k) });
        }
      } catch (e) { /* SecurityError on cross-origin frames, ignore */ }
      let handle = null;
      try {
        const m = (document.body.innerText || "").match(/\b1[3-9]\d\*{3,6}\d{2,4}\b/);
        if (m) handle = m[0];
      } catch (e) { /* ignore */ }
      return { items, origin: location.origin, handle };
    },
  });
  if (!results || !results.length) return { items: [], origin: null, handle: null };
  return results[0].result || { items: [], origin: null, handle: null };
}

// ── Main: harvest + upload ───────────────────────────────────────────

async function harvestAndUpload(engine, tab) {
  setStatus("info", "读取 cookies + localStorage …");
  const [cookies, ls] = await Promise.all([
    collectCookies(engine.cookieDomains),
    readLocalStorage(tab.id),
  ]);

  if (cookies.length === 0) {
    setStatus("err", "0 cookies — 你登过吗?先在这个标签页登一下再点。");
    return;
  }

  const origin = ls.origin || engine.primaryOrigin;
  const storage_state = {
    cookies,
    origins: ls.items.length ? [{ origin, localStorage: ls.items }] : [],
  };

  const cfg = await getConfig();
  if (!cfg.api || !cfg.token) {
    setStatus("err", "没配置 API / token。点底下「设置」填一下。");
    return;
  }
  // label 是服务端识别不出账号时的兜底匹配键,多人撞同一个默认值会被
  // 当成同一账号互相覆盖 —— 所以必须每人设置自己的标识才能上传。
  const label = (cfg.label || "").trim();
  if (!label || /^(anon|user-(mac|win|linux|unknown))$/.test(label)) {
    setStatus("err", "先在「设置」里填你自己的标识(如 alice-mac),不能用默认值。");
    return;
  }

  const ua = navigator.userAgent;
  const plat = navigator.platform;

  setStatus("info", `上传 → ${cfg.api} …`);
  try {
    const r = await fetch(`${cfg.api.replace(/\/$/, "")}/api/engine-sessions/upload`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Harvest-Token": cfg.token,
      },
      body: JSON.stringify({
        engine: engine.key,
        storage_state,
        source_label: label,
        account_handle: ls.handle || null,
        user_agent: ua,
        platform: plat,
        ttl_days: 7,
      }),
    });
    if (r.status === 200 || r.status === 201) {
      const data = await r.json();
      const who = data.account_handle ? ` 账号 ${data.account_handle}` : "";
      setStatus("ok",
        (data.renewed
          ? `✓ ${engine.name} 已续期老账号 #${data.id}${who}!`
          : `✓ ${engine.name} 新增账号 #${data.id}${who}!`) +
        ` cookies=${cookies.length} localStorage=${ls.items.length} 7 天后过期`);
    } else {
      const text = await r.text();
      setStatus("err", `✗ HTTP ${r.status}: ${text.slice(0, 200)}`);
    }
  } catch (e) {
    setStatus("err", `✗ 网络错误: ${e.message}`);
  }
}

// ── Boot ─────────────────────────────────────────────────────────────

async function init() {
  const tab = await getActiveTab();
  const engine = detectEngine(tab.url);
  const info = $("engine-info");
  const btn = $("harvest-btn");

  if (!engine) {
    info.className = "engine-unknown";
    info.innerHTML = `
      <div>当前页不是支持的 AI 引擎。先在新标签页打开:</div>
      <div style="margin-top: 6px; line-height: 1.8;">
        ${Object.values(ENGINES).map(e =>
          `<a href="${e.primaryOrigin}" target="_blank">${e.name}</a>`
        ).join(" · ")}
      </div>
      <div style="margin-top: 6px; font-size: 11px; color: #6b7280;">
        登录 + 发一句话试通,再回来点这个扩展。
      </div>
    `;
    btn.textContent = "先打开一个引擎页";
    btn.disabled = true;
    return;
  }

  info.className = "engine-detected";
  info.innerHTML = `<strong>检测到:</strong> ${engine.name} (<code>${engine.key}</code>)`;
  btn.textContent = `上传 ${engine.name} session`;
  btn.disabled = false;

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "处理中…";
    try {
      await harvestAndUpload(engine, tab);
    } finally {
      btn.disabled = false;
      btn.textContent = `重新上传 ${engine.name} session`;
    }
  });
}

$("open-options").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
$("show-engines").addEventListener("click", (e) => {
  e.preventDefault();
  const list = Object.values(ENGINES)
    .map(e => `  • ${e.name} (${e.primaryOrigin})`).join("\n");
  alert("支持的引擎:\n" + list);
});

init();
