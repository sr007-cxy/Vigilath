# URL 校验 case 清单(前后端对齐)

前端 `frontend/src/utils/validateUrl.ts` 与后端 `backend/geo/utils/validator.py::validate_url`
必须**对同一输入给出相同的 pass/reject 结果**。改规则前先改本清单,再同步两端。

## 规则

1. 输入可以不带 scheme;前端补 `https://` 后再校验。后端只接受补好的字符串。
2. scheme 必须是 `http` 或 `https`。
3. host 必须是 **ASCII LDH**(字母 / 数字 / 连字符 / 点),且至少包含一个点。
4. host 不能以 `-` / `.` 开头或结尾,每一段 label 长度 ≥ 1。
5. **不支持 IDN / 非 ASCII**。中文域名、emoji 域名一律拒绝。未来若要支持,需引入
   `idna.encode`(后端)+ `URL.hostname` punycode 形态(前端),并更新本清单。
6. IPv4 形如 `192.168.1.1` 因全 ASCII 数字+点,天然通过。IPv6 不支持(含 `:`,
   LDH 不允许)。
7. 允许带端口、路径、query,host 的校验只看 hostname 部分。

## 合法(应 pass)

| 输入 | 备注 |
|---|---|
| `example.com` | 裸域名,前端补 `https://` |
| `https://example.com` | 完整 URL |
| `http://example.com` | http scheme |
| `https://example.com/path?q=1` | 带路径与 query |
| `https://sub.example.com` | 子域 |
| `https://example.com:8080` | 带端口 |
| `moltspay.com` | 真实用例,曾跑过完整检测 |
| `192.168.1.1` | IPv4,纯 ASCII 数字+点 |
| `https://a-b.c-d.com` | LDH 中含 `-` |

## 非法(应 reject)

| 输入 | 理由 |
|---|---|
|(空字符串) | 空 |
| `hello` | 无点号 |
| `超响应` | 非 ASCII |
| `https://超响应` | 非 ASCII host |
| `淘宝.中国` | IDN,暂不支持 |
| `ftp://example.com` | scheme 非 http/https |
| `https://` | 无 host |
| `https://.com` | label 空 |
| `https://example-.com` | label 以 `-` 结尾 |
| `https://-example.com` | label 以 `-` 开头 |
| `javascript:alert(1)` | 非 http/https |
| `https://[::1]` | IPv6,含非 LDH 字符 |
| `https://example .com` | 含空格 |

## 测试方式

同一清单,前后端各跑一遍:

- 后端:`cd backend && .venv/bin/python -c "from geo.utils.validator import validate_url; ..."`
- 前端:`cd frontend && node -e "import('./src/utils/validateUrl.ts')..."`(或用 tsx)

两端结果必须逐行一致。
