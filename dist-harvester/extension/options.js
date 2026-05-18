// Options page: load + save API / token / label.

const DEFAULT_API = "http://123.125.194.100:12080";

function defaultLabel() {
  // Best-effort: derive from navigator.platform + locale.
  // 真实 hostname Chrome 扩展拿不到(隔离 web 环境),让用户自己改。
  const osTag = (navigator.platform || "").toLowerCase();
  let os = "unknown";
  if (osTag.includes("mac"))    os = "mac";
  else if (osTag.includes("win")) os = "win";
  else if (osTag.includes("linux")) os = "linux";
  return `user-${os}`;
}

function $(id) { return document.getElementById(id); }

function setStatus(kind, msg) {
  const el = $("status");
  el.className = kind;
  el.textContent = msg;
  setTimeout(() => { el.className = ""; el.textContent = ""; }, 3000);
}

function load() {
  chrome.storage.local.get(["api", "token", "label"], (data) => {
    $("api").value = data.api || DEFAULT_API;
    $("token").value = data.token || "";
    $("label").value = data.label || defaultLabel();
  });
}

function save() {
  const api = $("api").value.trim() || DEFAULT_API;
  const token = $("token").value.trim();
  const label = $("label").value.trim() || defaultLabel();

  if (!token) {
    setStatus("err", "Token 必填,跟运维要。");
    return;
  }

  chrome.storage.local.set({ api, token, label }, () => {
    if (chrome.runtime.lastError) {
      setStatus("err", "保存失败: " + chrome.runtime.lastError.message);
    } else {
      setStatus("ok", "✓ 已保存。回到引擎页点扩展按钮就能上传了。");
    }
  });
}

$("save").addEventListener("click", save);
load();
