// Minimal service worker — manifest v3 needs one even if we don't use it.
// 留空文件 Chrome 不喜欢,放一个 install 时打开 options 页的逻辑当占位。

chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") {
    // 首次装完自动打开设置页,引导填 API + token
    chrome.runtime.openOptionsPage();
  }
});
