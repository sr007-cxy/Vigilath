# GEO Session Harvester(Chrome 扩展)

让你**在自己的 Chrome 里登录国内 AI 引擎之后,一键把 session 上传到 GEO 中央 pool**。
这样服务器跑舆情监测时就能复用你的登录态,不用每个机器单独过 CAPTCHA。

**支持引擎**:豆包 / 通义千问 / DeepSeek / 文心一言 / 腾讯元宝(5 个)

**支持浏览器**:Chrome 88+、Edge 88+、Brave、其他 Chromium 衍生(Manifest V3)

---

## 一、安装(30 秒)

1. 把这个 `extension/` 文件夹下载到你电脑上(或下载完整 zip 解压)
2. 打开 Chrome,地址栏输 `chrome://extensions/`
3. 右上角打开**「开发者模式」**(开关)
4. 左上角点**「加载已解压的扩展程序」**,选刚才那个 `extension/` 文件夹
5. 扩展安装完会自动弹出**「设置」**页:
   - **API 地址**:默认 `http://123.125.194.100:12080`(测试环境),一般不用改
   - **Harvest Token**:跟运维要(`ENGINE_SESSION_HARVEST_TOKEN`)
   - **你的标识**:`alice-mac`,`bob-win` 之类,后台能看到谁贡献的
6. 点**「保存」**

装完后浏览器右上角应该多了一个 GEO 扩展图标(没有的话点拼图图标找它,然后 pin 一下)。

---

## 二、用法(每次约 1 分钟)

对**每个引擎**重复这套动作:

1. 新标签页打开引擎的 chat 页:
   - 豆包      https://www.doubao.com/chat/
   - 通义千问  https://www.qianwen.com/
   - DeepSeek  https://chat.deepseek.com/
   - 文心一言  https://yiyan.baidu.com/
   - 腾讯元宝  https://yuanbao.tencent.com/
2. **正常登录**(手机号 / 微信 / 等等)
3. 发一句 `你好` 试一下,确认能正常聊(如果弹滑块/图像 CAPTCHA 也人手过)
4. **回到这个标签页**,点右上角 GEO 扩展图标
5. 弹窗里会自动检测到引擎名(比如「检测到: 豆包」),点**「上传 [引擎名] session」**
6. 看到「✓ 上传成功」就行了。换下一个引擎重复。

---

## 三、什么时候要重新跑?

- **每 5-7 天**:服务器侧的 session 7 天后自动过期,过期前补就行
- **服务器侧出现 CAPTCHA 报警**:运维会通知,你重新登一次对应引擎再上传
- 某个引擎账号被风控了:那个号别用了,换个测试小号重跑

---

## 四、常见问题

### Q: 我点扩展按钮提示 "0 cookies"

说明这个标签页你没真的登进去 —— 网页上方还是登录按钮,或者你刚刚才打开页面 cookies 还没写完。先在网页里**真的登录 + 发一句话**确认能聊,再点扩展。

### Q: 上传报 "HTTP 401: invalid X-Harvest-Token"

token 输错了。点扩展弹窗里的「设置」重新填一次,**注意别复制到首尾空格**。

### Q: 上传报 "HTTP 422: storage_state has no cookies"

跟问题 1 一样,没真的登录。先登录再点扩展。

### Q: 服务器侧能看到我上传的吗?

```bash
curl http://123.125.194.100:12080/api/engine-sessions/pool-status
```

返回每个 engine 当前 active / quarantined / expired 数量。你上传的 row 会让对应 engine 的 active +1。

### Q: 这个扩展会偷我别的网站 cookie 吗?

不会。代码全在 [`popup.js`](popup.js) 里,只在你**主动点扩展按钮**时,**只**读取当前 tab 所在引擎域名下的 cookies,**只**上传到 options 页里你配置的 API。整个项目可以审计 —— 才几百行 JS。

### Q: 想撤销 / 删除?

`chrome://extensions/` 里把 GEO Session Harvester 点「移除」就行。`chrome.storage.local` 里存的 API + token + label 会跟着扩展一起删。**已经上传到服务器的 session 不会受影响**(那是服务器侧的数据)—— 要删找运维,或者直接等 7 天过期。

### Q: 我不用 Chrome,能不能用 Firefox / Safari?

- **Edge / Brave / Vivaldi / 等 Chromium**:可以,装法一样,manifest v3 通用
- **Firefox**:manifest v3 兼容,但 `chrome.cookies` 在 Firefox 里叫 `browser.cookies`,要小改。当前版本**不支持**,你帮我提 issue
- **Safari**:扩展生态完全不同(.appex bundle 要 Xcode 签名),**不支持**

---

## 五、隐私 + 安全

- 扩展**只在你点按钮的瞬间**读 cookies,平时不跑后台脚本
- 上传走 HTTPS / HTTP(取决于你 API 地址),body 里就是 Playwright 标准 `storage_state` JSON
- Token 存在 Chrome 自家的 `chrome.storage.local`(独立 sandbox,别的网站读不到)
- 你上传的 session 服务器侧默认 7 天过期,过期不再被 check-out
- 用**测试小号**,别用你日常工作号 —— 万一服务器侧 session 被乱用,有 blast radius
