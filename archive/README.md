# archive/

历史代码归档。**不参与 runtime**,仅供对比、回溯、回归测试用。

## 当前归档

### `geo_checker_v1_baseline.py`(8065 行)

2026-04-17 package 重构**之前**的单文件 CLI,从 git tag `pre-refactor-package-2026-04-17` 抽取得到,与该 tag 的根 `geo_checker.py` 逐字节相同。

**用途**:

- **评分 drift 回归测试**:把同一 URL 喂给 v1 baseline 和当前 backend,对比 24 个 category 的分数应完全一致
- **追查行为变化**:未来某次改动引入疑似评分变化时,用 baseline 做对照
- **upstream diff 参考**:该文件结构接近上游 `Yaqing2023/GEO` 的单文件 CLI;合 upstream 时可做 diff 基准

**不会被加载**:

- `pyproject.toml` 的 `[tool.setuptools.packages.find]` 限 `where = ["backend"]`,`archive/` 不会被打包
- Python 运行时从 `backend/` CWD 加载 `geo_checker` package,不会命中此文件
- 里面引用的 `_TeeStream` / `_write_text_pdf` 是**上游遗留的未定义符号**,`--report pdf/html` 路径会抛 `NameError`(已知缺陷,与 archive 无关)

### 独立运行(回归测试)

```bash
# 用 backend 的 venv(已有 requests + bs4)
backend/.venv/bin/python archive/geo_checker_v1_baseline.py https://moltspay.com

# 或者不用 venv
pip install requests beautifulsoup4
python archive/geo_checker_v1_baseline.py https://example.com
```

输出最后一段 `Category Breakdown` 表格就是逐 category 评分。可以用脚本解析后与当前 backend 的 `/api/check/anonymous` 响应对比。

### 已验证的对照点(2026-04-17)

| URL | v1 baseline | v2.1.0 backend | 差异 |
|---|---|---|---|
| `https://moltspay.com` | 80/100 (A),103.5/130 | 80/100 (A),103.5/130 | **0 category diff** |

详见 `docs/performance-report-2026-04-17.md` 与本次 commit 的 issue_list 更新。

## 归档规则

- 只放**完整、能独立运行**的快照,不放补丁或片段
- 文件名带 `v<major>_baseline` 或 `_pre_<重构名>` 后缀,一眼能看出归档时刻
- 绝不从 runtime 代码 `import` archive 里的东西 —— 它是只读历史
- 删除需通过 PR,理由写清楚(一般不删)
