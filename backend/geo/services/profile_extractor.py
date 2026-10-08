"""LLM extraction of brand materials -- maps raw text dropped in by the user (intro / website copy / PRD, etc.)
into BrandProfile fields in a structured way.

Reuses the same DeepSeek dual channel as content_generator (direct + OpenRouter fallback);
when neither key is configured the caller should return 503 to the frontend rather than silently generating empty materials.

Entry points:
- `extract_profile_from_text(text)` → (BrandProfile, model_id)
- `file_bytes_to_text(filename, data)` -> str (for PDF / DOCX / plain text uploads)

model_id == "" means the provider is unavailable; the caller returns 503 accordingly
"""
from __future__ import annotations

import io
import json
import logging
import os
import re

import requests

from geo.models.ai_telemetry import BrandProduct, BrandProfile
from geo.services.llm_gateway import post_and_record

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Per-call read timeout. Truncation retries make at most two calls, and the total must stay within nginx `proxy_read_timeout 300s`
DEFAULT_TIMEOUT = int(os.environ.get("GEO_PROFILE_EXTRACT_TIMEOUT", "140"))
# Chunked extraction (2026-09-17): parsed files keep at most MAX_EXTRACTED_TEXT (60k chars), split by paragraph into
# chunks <= CHUNK_CHARS, each extracted into its own JSON, then merged. Feeding the whole thing to the reasoning model was the root cause of production
# PDF upload 502s (neolink gateway 120s read timeout); later "truncate to 30k chars + halve on truncation" avoided timeouts,
# but lost the tail half of the materials. With chunking each chunk's input / output is small, losing no content and not hitting max_tokens.
CHUNK_CHARS = int(os.environ.get("GEO_PROFILE_EXTRACT_CHUNK_CHARS", "8000"))
CHUNK_WORKERS = int(os.environ.get("GEO_PROFILE_EXTRACT_WORKERS", "4"))


_SCHEMA_HINT = """\
请严格按下面 schema 输出 JSON(字段名 snake_case,缺失字段用空字符串 / 空数组,
不要凭空捏造):
{
  "profile_name": "...",
  "company_full_name": "...",
  "company_short_name": "...",
  "industry": "...",
  "entity_type": "service_tool | manufacturer | brand_owner | other",
  "core_business_lines": ["..."],
  "service_geo": "...",
  "website": "...",
  "contact_phone": "...",
  "contact_wechat": "...",
  "contact_email": "...",
  "creation_directions": ["..."],
  "copywriting_types": ["..."],
  "target_platforms": ["..."],
  "content_tones": ["..."],
  "content_redlines": ["..."],
  "team_size": "...",
  "founded_year": "...",
  "core_credentials": ["..."],
  "brand_diff_tags": ["..."],
  "core_service_overview": "...",
  "service_features": ["..."],
  "service_process": ["..."],
  "target_scenarios": ["..."],
  "service_guarantees": ["..."],
  "products": [
    {"name": "...", "aliases": ["..."], "category": "...", "selling_points": ["..."],
     "target_users": ["..."], "is_featured": false, "launch_date": "...", "price_range": "..."}
  ],
  "brand_story": "...",
  "key_person_story": "...",
  "case_stories": ["..."],
  "brand_values": "...",
  "available_materials": ["..."],
  "brand_slogan": "...",
  "core_message": "...",
  "extra_notes": "...",
  "seed_suggestions": ["...", "..."]
}

【核心原则:原文照搬,按段落分项,不删不缩】
所有 list 字段(包括但不限于 core_business_lines 核心业务线 / core_credentials 核心荣誉 /
case_stories 典型案例 / service_features / service_process / target_scenarios 等),都要遵守:
1. 原文里每一条独立的段落 / 项目符号 / 编号条目 = list 里独立的一项,一一对应,
   不要合并(比如原文 5 条荣誉,输出就必须是 5 条,不能压成 3 条)。
2. 每一项原文怎么写就怎么填,完整保留原句,不要缩写、不要总结、不要改写措辞;
   括号里的补充说明、年份、颁发机构、案例数据也都要带上。
3. 不要因为字数多就裁掉描述 — 哪怕一条荣誉占两三行字也要完整保留;
   除非超过 1000 字一条的极端长描述才适度截断。
4. 不要凭空合并相似条目;原文如果分成两段写,就分两条。
5. 反面例子:原文写「证券虚假陈述责任纠纷案件代理」,**不要**改写成
   「证券虚假陈述应对」/「证券虚假陈述」/「证券诉讼」等任何精简版本 —— 字字照搬原句。
   原文写「企业合规体系建设与刑事风险防控」,**不要**拆成
   「企业合规」+「刑事风险」两项,也**不要**简化成「合规建设」。

website / contact_phone / contact_wechat / contact_email 是【联系方式】,只从原文里照抄:
原文没写就留空字符串,**绝对不要推测、不要补全、不要生成示例号码或域名** ——
这四项会被直接写进对外发布的文章,编一个出来就是对客户的事故。

entity_type 是该主体的【业务主体类型】,据原文判断后**只输出下面四个英文 key 之一**:
- service_tool   服务业:SaaS、软件、在线平台、API、咨询 / 代运营等「提供服务或工具」的主体
- manufacturer   制造业:工厂、代工、设备 / 零部件 / 硬件制造,核心是「生产实体产品」
- brand_owner    品牌方 / 零售商:消费品牌、渠道商、代理 / 贸易,核心是「拥有品牌或卖货」
- other          不属于以上三类
判断不出来就留空字符串,不要乱猜;它会影响后续提示词的措辞方向。

products 是【本品牌自己的产品 / 型号清单】(消费品牌、硬件、软件版本都算;纯服务型主体没有就留空数组):
- 原文里每个有名字的具体产品 / 型号一条:name 填产品名或型号(如「<brand-e>手柄」「T4 Pro」),
  aliases 填原文出现过的别名 / 缩写 / 代号;**品牌名本身不是产品**,不要放进来
- category 填该产品的【通用品类名】(如「游戏手柄」「电动牙刷」「财税 SaaS」),不要填品牌名或型号
- selling_points / target_users 只从原文照抄,没写就留空数组
- 原文明确说是主推 / 旗舰 / 新品 / 重点推广的那一个 is_featured 填 true,其余 false;拿不准全 false
- launch_date / price_range 原文没写就留空字符串,不要推测;有的话也用字符串(如 "2024年3月" / "299-399元")
- 产品 / 型号的名称、卖点、参数**只**放进 products 对应的那一条,不要再复制到 core_business_lines /
  service_features / core_service_overview 里(那几项写业务线与服务,不写具体型号)

seed_suggestions 是给后续监测扩展用的「种子提示词」,要求:
- 3-8 条,每条 4-12 字
- 用真实用户会去问 AI 的口吻(短问 / 名词短语),不要写成长句
- 围绕本品牌的核心业务 / 用户痛点 / 行业关键词;避开品牌名本身
- 若 products 里有 is_featured 的主推产品,可以含 1 条它的 name 作种子(系统会按其品类扩词)
- 例子:跨境并购 / 律所推荐 / 遗产继承 / 跨境支付

只输出一个 JSON 对象,不要加 ``` 围栏,不要写解释文本。
"""

SYSTEM_PROMPT = (
    "你是品牌资料分析师,把用户给的原始资料(公司简介 / 官网文案 / PRD / 产品说明 / "
    "市场材料等)按【原文照搬、按段落分项】的方式映射到「品牌资料」JSON,"
    "并顺手给出几条可用作 AI 监测的「种子提示词」。\n"
    "你的任务是把原文里已有的内容搬到对应字段,而不是当编辑去改稿、压缩或润色 — "
    "用户后续会看到 list 里每一项,所以条目数和原文措辞必须保留。\n"
    + _SCHEMA_HINT
)


def _resolve_provider() -> tuple[str | None, str, str]:
    """returns (provider, model_id, api_key); provider=None means unavailable."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return "deepseek", DEEPSEEK_MODEL, ds_key
    if or_key:
        return "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    return None, "", ""


def _split_chunks(text: str, size: int) -> list[str]:
    """Split by paragraph (blank lines / newlines) into chunks of <= size chars; hard-split single overlong paragraphs. Order preserved."""
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    paras = [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        p = p.strip()
        while len(p) > size:                      # A single paragraph is already too long (PDF pages without newlines)
            if buf:
                chunks.append(buf); buf = ""
            chunks.append(p[:size]); p = p[size:]
        if buf and len(buf) + 1 + len(p) > size:
            chunks.append(buf); buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


def _extract_chunk(text: str, *, idx: int, total: int, provider: str, model_id: str,
                   url: str, headers: dict, _retry: bool = True) -> dict:
    """One chunk of raw material -> parsed dict (not coerced).

    _retry: when output is truncated by max_tokens (finish_reason=length), halve this chunk and try again, retrying only once.
    With chunking this path is rarely taken; kept as insurance.
    """
    hint = ""
    if total > 1:
        hint = (f"(这是同一份资料的第 {idx + 1}/{total} 段,只抽本段里出现的内容,"
                f"本段没有的字段留空字符串 / 空数组,不要根据前后文猜)\n\n")
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"以下是原始资料,请抽取为品牌资料 JSON:\n\n{hint}{text}"},
        ],
        "temperature": 0.2,
        # Verbatim-style extraction; output is the same order of magnitude as input; 8k chars per chunk is plenty. The gateway accepts 16000 for v4-flash (measured)
        "max_tokens": 12000,
        "response_format": {"type": "json_object"},
    }
    if provider == "deepseek":
        # In production DEEPSEEK_MODEL is a reasoning model (deepseek-v4-flash). Extracting fields is "copy and organize" and needs no reasoning;
        # with thinking on, tens of thousands of input chars eat the whole read timeout (on 2026-09-17 production extract-file had two 502s,
        # both Read timed out); same handling as suggest_terms: turn it off.
        payload["thinking"] = {"type": "disabled"}
    r = post_and_record(url, payload, headers, provider=provider,
                        feature="profile_extract", timeout=DEFAULT_TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"unexpected {provider} response shape: {e}")
    if choice.get("finish_reason") == "length" and _retry and len(text) > 2000:
        log.warning("profile extract: chunk %d/%d output truncated (input %d chars), retry with half",
                    idx + 1, total, len(text))
        return _extract_chunk(text[: len(text) // 2], idx=idx, total=total, provider=provider,
                              model_id=model_id, url=url, headers=headers, _retry=False)
    return _parse_json_loose(content)


# Short fields that "take the first non-empty" when merging (identity / contact type; answers across chunks should match, take the first to appear)
_FIRST_WINS = frozenset((
    "profile_name", "company_full_name", "company_short_name", "industry", "service_geo",
    "website", "contact_phone", "contact_wechat", "contact_email", "team_size", "founded_year",
    "brand_slogan",
))


def _merge_parsed(parts: list[dict]) -> dict:
    """Merge multi-chunk dicts into one: list fields concatenated in order and deduped; short fields take the first non-empty;
    long text fields (intro / story / notes) concatenate distinct content from each chunk; entity_type takes the majority vote."""
    out: dict = {}
    votes: dict[str, int] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for k, v in part.items():
            if v is None or v == "" or v == []:
                continue
            if k == "entity_type":
                key = _normalize_entity_type(v)
                if key:
                    votes[key] = votes.get(key, 0) + 1
                continue
            cur = out.get(k)
            if k == "products":
                # Products are list[dict] and use a dedicated merge (the generic list branch would stringify dicts, see _coerce_products)
                out[k] = _merge_products(cur if isinstance(cur, list) else [], v)
                continue
            if isinstance(v, list) or isinstance(cur, list):
                items = list(cur or []) if isinstance(cur, list) else ([cur] if cur else [])
                seen = {str(x).strip() for x in items}
                for x in (v if isinstance(v, list) else [v]):
                    xs = str(x).strip()
                    if xs and xs not in seen:
                        seen.add(xs); items.append(xs)
                out[k] = items
            elif k in _FIRST_WINS:
                if not cur:
                    out[k] = v
            else:
                vs = str(v).strip()
                if not cur:
                    out[k] = vs
                elif vs and vs not in str(cur):
                    out[k] = f"{cur}\n\n{vs}"
    if votes:
        out["entity_type"] = max(votes.items(), key=lambda kv: kv[1])[0]
    return out


def extract_profile_from_text(text: str) -> tuple[BrandProfile, list[str], str]:
    """returns (profile, seed_suggestions, model_id); model_id="" means the provider is unavailable.

    Long materials are extracted in parallel chunks then merged (see CHUNK_CHARS); a single chunk failure only loses that chunk, raising only if all fail.
    """
    provider, model_id, api_key = _resolve_provider()
    if not provider:
        return BrandProfile(), [], ""

    if provider == "deepseek":
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        url = OPENROUTER_URL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com",
            "X-Title": "GEO Profile Extractor",
        }

    chunks = _split_chunks(_truncate(text), CHUNK_CHARS)
    if not chunks:
        return BrandProfile(), [], model_id
    kw = dict(total=len(chunks), provider=provider, model_id=model_id, url=url, headers=headers)
    parts: list[dict] = []
    errors: list[str] = []
    if len(chunks) == 1:
        parts.append(_extract_chunk(chunks[0], idx=0, **kw))
    else:
        log.info("profile extract: %d chars → %d chunks", sum(len(c) for c in chunks), len(chunks))
        from concurrent.futures import ThreadPoolExecutor
        from geo.services.llm_usage import submit as _submit     # Carries contextvars, so usage attribution is not lost
        with ThreadPoolExecutor(max_workers=max(1, min(CHUNK_WORKERS, len(chunks)))) as pool:
            futs = [_submit(pool, _extract_chunk, c, idx=i, **kw) for i, c in enumerate(chunks)]
            for i, f in enumerate(futs):
                try:
                    parts.append(f.result())
                except Exception as e:  # noqa: BLE001 -- one bad chunk does not take down the whole document
                    errors.append(f"第 {i + 1} 段: {e}")
                    log.warning("profile extract: chunk %d/%d failed: %s", i + 1, len(chunks), e)
        if not parts:
            raise RuntimeError("所有分段都抽取失败: " + "; ".join(errors)[:300])
    parsed = _merge_parsed(parts) if len(parts) > 1 else (parts[0] if isinstance(parts[0], dict) else {})
    safe = _coerce_to_profile_fields(parsed)
    # Normalize entity_type to an enum key (the model may answer in Chinese or with synonyms); invalid / empty -> ""
    safe["entity_type"] = _normalize_entity_type(parsed.get("entity_type"))
    try:
        profile = BrandProfile(**safe)
    except Exception as e:  # noqa: BLE001
        # Only degrade when it really fails -- previously any overlong list triggered max_length validation failure,
        # emptying the whole profile; after coerce this path is rarely taken
        log.warning("BrandProfile mapping failed, degrading to empty materials: %s; raw=%s", e, str(parsed)[:300])
        profile = BrandProfile()
    # seed_suggestions: seed term candidates from the LLM, cleaned + deduped + length-limited (1-30 chars, at most 8)
    raw_seeds = parsed.get("seed_suggestions") or []
    seeds: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_seeds, list):
        for s in raw_seeds:
            if not isinstance(s, str):
                continue
            v = s.strip()
            if 1 <= len(v) <= 30 and v not in seen:
                seen.add(v)
                seeds.append(v)
            if len(seeds) >= 8:
                break
    return profile, seeds, model_id


_ENTITY_TYPE_KEYS = ("service_tool", "manufacturer", "brand_owner", "other")
_ENTITY_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "service_tool": ("service_tool", "服务业", "服务工具", "服务", "工具", "软件", "saas", "平台",
                     "tool", "software", "platform", "api", "在线服务", "咨询", "代运营"),
    "manufacturer": ("manufacturer", "制造业", "生产制造商", "制造商", "厂商", "厂家", "工厂", "制造",
                     "代工", "oem", "factory", "设备", "零部件", "硬件"),
    "brand_owner": ("brand_owner", "品牌方", "品牌", "零售", "零售商", "经销", "代理",
                    "贸易", "brand", "retailer"),
    "other": ("other", "其他", "其它"),
}


def _normalize_entity_type(raw) -> str:
    """Normalize the model's entity_type to one of 4 enum keys; unrecognized -> "" (let the user pick manually in the form)."""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if s in _ENTITY_TYPE_KEYS:
        return s
    for key, aliases in _ENTITY_TYPE_ALIASES.items():
        if any(a in s for a in aliases):
            return key
    return ""


def _coerce_products(raw) -> list[dict]:
    """LLM products -> a clean list of dicts: keep only dicts with a name, validate each against BrandProduct, at most 30.

    Generic list handling str()s each item (the fallback for list[str] fields), which for list[dict] fields turns
    dicts into "{'name': ...}" strings, failing BrandProfile validation and reverting the whole profile to an empty form -- hence this separate path.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for x in raw:
        if not isinstance(x, dict) or not str(x.get("name") or "").strip():
            continue
        try:
            out.append(BrandProduct(**x).model_dump())
        except Exception:  # noqa: BLE001
            continue
        if len(out) >= 30:
            break
    return out


def _merge_products(cur: list, new) -> list[dict]:
    """Merge products extracted from multiple chunks by name (casefold): list fields take the union, is_featured takes OR, short fields first come first served.

    _merge_parsed (merging parallel chunk extraction) routes the products key here, not through the generic list branch --
    otherwise chunk merging would stringify product dicts, see the note on _coerce_products.
    """
    merged: list[dict] = [dict(p) for p in _coerce_products(cur)]
    idx = {p["name"].strip().casefold(): i for i, p in enumerate(merged)}
    for p in _coerce_products(new):
        key = p["name"].strip().casefold()
        if key not in idx:
            idx[key] = len(merged)
            merged.append(p)
            continue
        m = merged[idx[key]]
        for lk in ("aliases", "selling_points", "target_users"):
            seen = {str(x).casefold() for x in m.get(lk) or []}
            for x in p.get(lk) or []:
                if str(x).casefold() not in seen:
                    seen.add(str(x).casefold())
                    m.setdefault(lk, []).append(x)
        m["is_featured"] = bool(m.get("is_featured")) or bool(p.get("is_featured"))
        for sk in ("category", "launch_date", "price_range", "notes"):
            if not m.get(sk) and p.get(sk):
                m[sk] = p[sk]
    return merged[:30]


def _coerce_to_profile_fields(parsed: dict) -> dict:
    """Force-fit LLM output to the BrandProfile schema:
      - list fields over max_length are truncated to the limit
      - LLM gave a string but the schema wants a list -> split into a single-element list
      - LLM gave a list but the schema wants a string -> join into multiple lines
      - None / unexpected types -> dropped (use defaults)
    Prevents a single field's hard failure from zeroing out the whole profile.
    """
    out: dict = {}
    for name, field in BrandProfile.model_fields.items():
        if name not in parsed:
            continue
        raw = parsed[name]
        if raw is None:
            continue
        if name == "products":
            out[name] = _coerce_products(raw)
            continue
        ann = field.annotation
        # Get max_length (Field(..., max_length=N))
        max_len = None
        for m in (field.metadata or []):
            ml = getattr(m, "max_length", None)
            if ml is not None:
                max_len = ml
                break

        is_list_field = _is_list_annotation(ann)
        if is_list_field:
            if isinstance(raw, list):
                items = [str(x).strip() for x in raw if x is not None and str(x).strip()]
            elif isinstance(raw, str):
                s = raw.strip()
                items = [s] if s else []
            else:
                continue
            if max_len is not None and len(items) > max_len:
                items = items[:max_len]
            out[name] = items
        else:
            # String field
            if isinstance(raw, list):
                s = "\n".join(str(x).strip() for x in raw if x is not None and str(x).strip())
            else:
                s = str(raw)
            if max_len is not None and len(s) > max_len:
                s = s[:max_len]
            out[name] = s
    return out


def _is_list_annotation(ann) -> bool:
    """Determine whether a Pydantic field type is list[...]."""
    import typing
    origin = typing.get_origin(ann)
    return origin in (list, typing.List)


# --------------- Uploaded file -> plain text -------------------------


# Cap matches the reasonable bound of BrandProfile.extra_notes: an LLM can digest ~30K Chinese chars at once
MAX_EXTRACTED_TEXT = 60_000

# Friendly error message body; the caller wraps it in an HTTPException
class FileParseError(Exception):
    pass


SUPPORTED_EXTS = ("txt", "md", "docx", "pdf")


def file_bytes_to_text(filename: str, data: bytes) -> str:
    """Dispatch to the parser by extension; returns text truncated to MAX_EXTRACTED_TEXT.

    Supported:
    - .docx -> python-docx paragraphs + tables
    - .pdf  -> pypdf text by page + paragraph (scanned / image PDFs yield no text and raise FileParseError)
    - .txt / .md -> utf-8 / gbk fallback

    Other types (.doc / .csv / .json ...) all get 415, prompting the user to copy content into the text box
    or save as a supported format.
    """
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""

    if ext == "docx":
        return _truncate(_parse_docx(data))
    if ext == "pdf":
        return _truncate(_parse_pdf(data))
    if ext in ("txt", "md"):
        try:
            return _truncate(data.decode("utf-8"))
        except UnicodeDecodeError:
            try:
                return _truncate(data.decode("gbk", errors="replace"))
            except Exception as e:  # noqa: BLE001
                raise FileParseError(f"无法以 UTF-8 或 GBK 解码文件:{e}") from e
    if ext == "doc":
        raise FileParseError(
            "暂不支持 .doc 旧二进制格式,请用 Word 「另存为」.docx 后重试"
        )
    raise FileParseError(
        f"暂不支持的文件类型(.{ext or '未知'}),支持 {', '.join('.' + e for e in SUPPORTED_EXTS)}"
    )


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) > MAX_EXTRACTED_TEXT:
        return text[:MAX_EXTRACTED_TEXT]
    return text


def _parse_docx(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as e:
        raise FileParseError("后端缺少 python-docx 依赖,无法解析 Word") from e
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise FileParseError(f"Word 解析失败(文件可能损坏):{e}") from e
    paras = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    # Extract tables too -- PRDs / company intros often list fields in tables
    tables: list[str] = []
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                tables.append(" | ".join(cells))
    out = "\n".join(paras + tables).strip()
    if not out:
        raise FileParseError("这份 Word 文档为空 / 没有可抽取的文字")
    return out


def _parse_pdf(data: bytes) -> str:
    """pypdf extracts text by page, keeping in-page paragraph line breaks -- the LLM later relies on them to recognize "items by paragraph".

    Scanned / image PDFs yield no characters and end in FileParseError, so the frontend prompts the user to use Word/text.
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise FileParseError("后端缺少 pypdf 依赖,无法解析 PDF") from e
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise FileParseError(f"PDF 解析失败(文件可能损坏 / 加密):{e}") from e
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise FileParseError("PDF 已加密,请先解除密码保护再上传")
    pages: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        t = t.strip()
        if t:
            pages.append(t)
    out = "\n\n".join(pages).strip()
    if not out:
        raise FileParseError(
            "这份 PDF 抽不到任何文字(可能是扫描件 / 图片版),"
            "请用 Word 重新导出 .docx,或复制内容粘贴到文本框"
        )
    return out


def _parse_json_loose(text: str) -> dict:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.lstrip("`").lstrip()
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    try:
        v = json.loads(s)
        if isinstance(v, dict):
            return v
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, dict):
                return v
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"failed to parse JSON from LLM output: {s[:200]}")


# -- Lightweight: extract only "industry/category terms", for the audit page's "extract industry terms for me" --------------
#
# Do not use extract_profile_from_text -- it extracts the whole profile (a dozen-plus fields), with large output, taking
# 26-53s. Here the model only emits 6-8 category terms, a few hundred tokens of output, usually back in 3-8s.
def suggest_industry_terms_llm(brand: str, text: str = "") -> tuple[list[str], str]:
    """Returns (terms, model_id). model_id="" means no LLM key configured."""
    provider, model_id, api_key = _resolve_provider()
    if not provider:
        return [], ""
    if provider == "deepseek":
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    else:
        url = OPENROUTER_URL
        headers = {
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com", "X-Title": "GEO Term Suggest",
        }
    sys_p = (
        "你是行业分析助手。只输出该品牌所属的『行业/品类关键词』—— 就是用户在 AI 搜索里"
        "会问的品类词(例:「扫地机器人」「气泡水」「无人机」),**不要品牌名本身、不要公司名**。"
        '严格输出 JSON:{"terms": ["词1","词2", ...]},6-8 个,由宽到窄,每个不超过 12 字。'
    )
    brand = (brand or "").strip()
    text = (text or "").strip()[:1500]
    user = f"品牌名称:{brand}\n" + (f"官网正文节选:\n{text}\n" if text else "") + "请给出品类关键词。"
    payload = {
        "model": model_id,
        "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
    }
    if provider == "deepseek":
        # In production DEEPSEEK_MODEL is a reasoning model (deepseek-v4-flash), and thinking tokens count toward max_tokens:
        # on 2026-09-14 production had 9 failures and 2 successes within 24h, all failures "thinking ate the budget, not a word of body output"
        # (finish_reason=length, empty content). 8 category terms need no reasoning -- with it off, measured
        # 1.8s with stable JSON; with it on, anywhere from 2.8s (lucky short thinking) to 13s (1100 thinking tokens).
        payload["thinking"] = {"type": "disabled"}
    r = post_and_record(url, payload, headers, provider=provider,
                        feature="term_suggest", timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    choice = r.json()["choices"][0]
    content = choice["message"]["content"]
    if not (content or "").strip() and choice.get("finish_reason") == "length":
        # Do not let it end up as "failed to parse JSON: <empty>" -- that log took ages to recognize as truncation
        raise RuntimeError("LLM 正文为空且 finish_reason=length:输出预算被思考占满")
    parsed = _parse_json_loose(content)
    terms = parsed.get("terms") if isinstance(parsed, dict) else None
    if not isinstance(terms, list):
        terms = []
    out = [str(t).strip() for t in terms if str(t).strip()]
    return out, model_id
