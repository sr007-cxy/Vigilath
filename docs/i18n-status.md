# 前端双语（zh / en）支持现状

> 文档版本：v1.1
> 日期：2026-04-15
> 负责人：Oliver
> 适用范围：`frontend/` React SPA（vigilath.com）

---

## 一、基本定位

- **仅支持两种语言**：简体中文（`zh`）、英文（`en`）。
- **i18n 资源位置**：**`frontend/src/i18n/index.ts`**，所有翻译资源 inline 在 `i18n.init({ resources })` 里，en 和 zh 两个子树，当前约 835 keys × 2 语言对齐。
- **只有一个真实源**：2026-04-15 已删除历史遗留的孤儿文件 `frontend/src/i18n/locales/{en,zh}/translation.json`，避免"加 key 到 JSON 但不生效"的陷阱。`i18n/index.ts` 顶部已加防御性注释明确这一点。任何新增 key 只能加在 `i18n/index.ts` 的 en / zh 子树里。
- **默认语言**：看用户浏览器 / localStorage；`Header.tsx` 顶部的切换按钮只在 `zh` / `en` 间来回切。

---

## 二、死代码 & 配置错配（应立即清理）

| 位置 | 问题 | 处置建议 |
|---|---|---|
| `i18n/index.ts` 1000–1459 行 | `ja` / `ko` / `de` / `fr` / `es` 五个语言块各约 92 行全是 stub，只翻了几条 nav / auth key | 直接删除这五个块 |
| `components/LanguageSwitcher.tsx:12-20` | 下拉暴露 7 种语言（英/中/日/韩/德/法/西），但只有 en/zh 有完整翻译 | 要么裁剪到 `en` / `zh`，要么整个组件删掉 |
| `components/Navbar.tsx` | 全仓库无任何 import，已被 `Header.tsx` 取代 | 整个文件删除 |
| `LanguageSwitcher.tsx` 的使用方 | 只剩 `Contact.tsx` 和 dead `Navbar.tsx` 引用；`Header.tsx` 用自己的一套 zh/en 切换 | 删 `Navbar` 后，`Contact.tsx` 改用 `Header` 顶部的切换，统一体验 |

---

## 三、双语覆盖缺口

### A. 整页 / 大段未本地化（🔴 严重）

| 文件 | 问题 | 参考行 |
|---|---|---|
| `pages/Advanced.tsx` | 付费高级页近 40 处硬编码中文 UI 文本：`"类目"` / `"总分"` / `"目标域名"` / `"robots.txt 规则"` / `"Common Crawl 索引"` / `"知识图谱覆盖"` / `"情感与框架"` / `"内容缺口"` 等。整页只有 16 处 `t()` 调用 | 693, 700, 706, 769, 803–935, 1041–1404 |
| `pages/GeoKnowledge.tsx` | 一整段中文 GEO 介绍直接写在 JSX 里（"GEO（Geographic Optimization）是一种..."），没抽 key | 38 |

### B. 无 `useTranslation` 的用户可见文本（🟠 中等）

| 位置 | 硬编码文本 |
|---|---|
| `components/ThemeToggle.tsx:33` | `title={isDark ? '切换到亮色模式' : '切换到暗色模式'}`，整个组件没 import i18n |
| `pages/Register.tsx:33` | `setError('请同意服务条款和隐私政策')` |
| `pages/Register.tsx:44` | 英文 fallback `'Registration failed'` |
| `pages/ForgotPassword.tsx:30` | 前端仍把后端返回的 `reset_token` 显示到页面上。目前后端已经不返回该字段，死分支但应清理（安全面：避免被误打开） |

### C. 服务层错误消息未走 i18n（🟠 中等）

所有以下 fallback 都是英文硬编码，`t()` 未参与：

- `pages/ProductsServices.tsx:72` → `'Failed to load memberships'`
- `pages/CheckoutPending.tsx:77` → `'Failed to load plan'`
- `pages/Home.tsx:103` → `'Failed to load plans'`
- `pages/Account/MembershipTab.tsx:41` → `'Failed to load membership'`
- `pages/Account/HistoryTab.tsx:38 / 56 / 71` → `'Failed to load history'` / `'Failed to load record'` / `'Failed to delete'`
- `pages/Account/UsageTab.tsx:40` → `'Failed to load usage'`
- `components/PaymentModal.tsx:48 / 76` → `'Failed to load plans'` / `'Payment failed'`

**统一方案**：新增 `common.errors.loadFailed{Entity}` / `common.errors.paymentFailed` / `common.errors.deleteFailed` 等 key，所有 catch 分支改走 `t()`。

### D. `t(key, '中文默认值')` 反模式（🟡 待验证）

`Account/AccountLayout.tsx`、`Account/*Tab.tsx`、`CheckoutPending.tsx` 里大量写法：

```ts
t('account.menu.profile', '个人资料')
t('checkoutPending.title', '待支付订单')
```

默认值是中文。**只要 en 翻译块里对应 key 齐全，就没事**；如果缺 key，英文用户会回落到中文。

这些 key 是 commit `1b77722 feat(account)` 新加的功能，**en 块里是否同步补齐未核对**。需要专项跑一次 en ↔ zh key diff。

### E. 后端错误消息未接入 i18n（🟡 轻微）

- 后端已有 `backend/geo/utils/error_i18n.py` 错误 i18n 框架
- 但 `backend/geo/api/auth.py` 里仍抛裸英文消息：`"Email not found"` / `"User not found"` / `"Invalid reset token"`
- 前端 `services/apiError.ts` 收到后原样展示，不参与 react-i18next

### F. 可接受不翻译（不算 bug）

| 位置 | 内容 | 原因 |
|---|---|---|
| `Header.tsx:158` | `'中文'` / `'EN'` | 语言切换按钮用语言的原生名是通用模式 |
| `Header.tsx` / `Navbar.tsx` / 站点 logo | `"GEO Checker"` | 品牌名不本地化 |

---

## 四、整改优先级与路线

| 顺序 | 项 | 预估工作量 | 理由 |
|---|---|---|---|
| **P0** | 清理 ja/ko/de/fr/es 死代码（第二节） | 0.5h | 低风险高收益，避免用户切到残缺语言 |
| **P0** | en ↔ zh key 完整性 diff（D 项） | 0.5h 写脚本 + 补差 | 当前无法保证英文用户能看到完整文案 |
| **P1** | `Advanced.tsx` 整页补 i18n（A-1） | 2–3h | 付费高级功能的体验门面 |
| **P1** | `GeoKnowledge.tsx` 抽 key（A-2） | 0.5h | 首页 nav 导入，曝光高 |
| **P2** | 服务层错误消息统一走 i18n（C） | 1–2h | 系统性提升一致性 |
| **P2** | `ThemeToggle` / `Register` / `ForgotPassword` 三处硬编码（B） | 0.5h | 顺手修 |
| **P3** | 后端 `auth.py` 错误消息接入 `error_i18n.py`（E） | 1h | 跨端一致，但次要 |

---

## 五、核对方法备忘

```bash
# 找出 src 下所有硬编码中文（排除 i18n 源文件和注释行）
cd frontend/src
grep -rnP "[\x{4e00}-\x{9fff}]" --include="*.tsx" --include="*.ts" . \
  | grep -v "i18n/index.ts" \
  | grep -v "^\s*[0-9]*:\s*//"

# 列出所有未 import useTranslation 的页面 / 组件
grep -rL "useTranslation" frontend/src/pages frontend/src/components \
  --include="*.tsx"

# en ↔ zh key diff（待写脚本，建议用 ts-node 或 node -e）
```

---

## 六、变更记录

- **2026-04-15 · v1.0**：首次建档，基线审计（commit `cf91519`）。
- **2026-04-15 · v1.1**：删除孤儿文件 `src/i18n/locales/{en,zh}/translation.json`，并在 `i18n/index.ts` 顶部加防御性注释。起因：上游 `5099ef1` 往孤儿 JSON 里加 `result.shareExport.downloadReport` key，线上直接显示原始占位符（`a586871` 之后由 `5dc3d7b` 补到真实源修复）。根治方案：从此只有一个 i18n 源。
- **2026-04-15 · v1.2**：后端 `check.message` 双语大迁移启动（commit `4c38e4b` 开始）。详见第七节。

---

## 七、check.message 结构化 i18n 架构

用户在 `/result` 页面看到的每条检查结果的文案（`CheckResult.message`）原本是 `geo_checker.py` 里 `print(f"  [{PASS}] ...")` 直接打印的英文字符串，由后端正则 parser 抓成 `CheckResult` 对象。这批字符串过去**无法跟随 zh/en 切换**——后端只知道一种英文原文。

### 方案 C：后端混合 emit（已实施）
引入一个 helper `emit_check(status_tag, key, message, params)` 同时做两件事：
1. `print()` 原来的英文字符串（CLI 行为保持不变）
2. 在字符串末尾追加一段 ASCII-invisible 的 `\x01GK\x01{json}\x01GE\x01` marker，包含 `{k: "i18n key", p: {...params}}`

只有当 `GEO_EMIT_STRUCTURED=1` 环境变量存在时才追加 marker。后端 `services/geo_checker.py` 在 import `geo_checker.__main__` **之前**把这个 env var 设好；CLI 用户不设，终端输出保持干净。

后端 parser `parse_geo_output` 扩展了一个 `_extract_key_marker()` 函数，从 message 末尾剥离 marker 并把 `message_key` / `message_params` attach 到 `CheckResult`。前端 `Result.tsx` 的 `renderCheckMessage(check, t)` helper：有 key 则 `t(key, params)`，无 key 则回落 `message`。

### 关键文件
| 文件 | 作用 |
|---|---|
| `backend/geo_checker/__main__.py` | **生产 check 真源**（backend 实际 import）。emit_check helper + 23 个 check 函数 |
| `geo_checker/__main__.py` | Standalone CLI 副本，与 backend 版本**已分叉**。emit_check 已同步但其它检查函数不保证同步 |
| `geo_checker.py` | Root 标量文件版本，advanced modes 额外用这个。emit_check 已同步 |
| `backend/geo/services/geo_checker.py` | `parse_geo_output` + marker 抽取逻辑 + `os.environ["GEO_EMIT_STRUCTURED"] = "1"` |
| `backend/geo/models/geo.py` | `CheckResult` 加 `message_key` / `message_params` 可选字段 |
| `frontend/src/types/geo.ts` | 对应的 TS 接口字段 |
| `frontend/src/pages/Result.tsx` | `renderCheckMessage()` helper + 渲染路径 |
| `frontend/src/i18n/index.ts` | `result.checks.{category}.{message_slug}` 子树 |

### 迁移进度（截至 commit `c1e8344`）
**已完成** (8 / 23 类):
- ✅ HTTPS
- ✅ robots.txt
- ✅ llms.txt
- ✅ .well-known Discovery
- ✅ sitemap.xml
- ✅ Meta Tags
- ✅ Mobile-Friendliness & Page Weight
- ✅ Structured Data

**匿名/免费档 (`FREE_CHECK_CATEGORIES` 的 5 个) 已全部覆盖**——example.com anonymous check 的 14/14 条 message 都带 i18n key。

**待迁移** (15 / 23 类):
- Search Engine & AI Platform Registration
- Content Accessibility
- AI Crawl Readiness
- Content Quality for AI
- Technical Crawlability
- Authority & Trust Signals
- AI-Specific Optimization
- Social Signals
- AI Answer Format Optimization
- Schema Breadcrumbs & Knowledge Panel
- URL Normalization
- Outbound Links & Media
- Multilingual Content Depth
- Cross-Platform Content Distribution
- Multi-Page Sampling

### 迁移模板
每个 check 函数里的每条 `print(f"  [{STATUS}] ...")` 替换为：

```python
emit_check(STATUS, "result.checks.{category_slug}.{message_slug}", "English fallback", {"param_name": value})
```

对应地在 `frontend/src/i18n/index.ts` 的 en 和 zh 两个 `result.checks.{category_slug}` 子树下添加 key，使用 `{{param_name}}` 插值。每次改完跑一次 key parity 校验脚本，确保 en/zh 数量相等。

### 向后兼容保证
任何还未迁移的 check 函数继续走 legacy 英文 print 路径。parser 找不到 marker 时 `message_key=None`、`message_params=None`，前端回落到 raw `message` 字符串（英文）。所以**迁移可以按类目独立推进，每次提交都是完整可部署状态**。

### 待办
1. **剩余 15 类 check 迁移**（每类 0.5–1 小时）
2. **两份 geo_checker 源的合并**：`backend/geo_checker/__main__.py` 和 `geo_checker/__main__.py` 现在已经分叉（CHECK_REGISTRY 只在 backend 版有，--citation-check CLI 只在 root 版有）。长期需要合并成一份
3. **Standalone CLI 同步**：迁移后 CLI 用户仍然看英文（marker 被 env var 关掉）——这是预期行为，但 CLI 自带 i18n 是另一个话题
