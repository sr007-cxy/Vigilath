// Options page: load + save API / token / label.

const DEFAULT_API = "http://test.example.com:12080";

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
    // 不预填 user-mac 这类通用默认值 —— label 现在是账号匹配键,必须每人唯一
    $("label").value = data.label || "";
  });
}

function save() {
  const api = $("api").value.trim() || DEFAULT_API;
  const token = $("token").value.trim();
  const label = $("label").value.trim();

  if (!token) {
    setStatus("err", "Token 必填,跟运维要。");
    return;
  }
  // label 是服务端兜底的账号匹配键,多人撞默认值会互相覆盖登录态
  if (!label || /^(anon|user-(mac|win|linux|unknown))$/.test(label)) {
    setStatus("err", "标识必填,填能认出你的名字(如 alice-mac),别用默认值。");
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
