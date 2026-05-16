"""LLM 抽取品牌画像 — 把用户拖入的原始文本(简介 / 官网文案 / PRD 等)
结构化映射到 BrandProfile 字段。

复用与 content_generator 相同的 DeepSeek 双通道(直连 + OpenRouter fallback);
两个 key 都没配时调用方应给前端 503,而不是无声生成空画像。

入口:
- `extract_profile_from_text(text)` → (BrandProfile, model_id)
- `file_bytes_to_text(filename, data)` → str(用于 PDF / DOCX / 纯文本上传)

model_id == "" 表示 provider 不可用,调用方据此返回 503
"""
from __future__ import annotations

import io
import json
import logging
import os
import re

import requests

from geo.models.ai_telemetry import BrandProfile

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = int(os.environ.get("GEO_PROFILE_EXTRACT_TIMEOUT", "120"))


_SCHEMA_HINT = """\
请严格按下面 schema 输出 JSON(字段名 snake_case,缺失字段用空字符串 / 空数组,
不要凭空捏造,但可以从原文里合理归纳):
{
  "profile_name": "...",
  "company_full_name": "...",
  "company_short_name": "...",
  "industry": "...",
  "core_business_lines": ["..."],
  "service_geo": "...",
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
  "target_audience": ["..."],
  "user_pain_points": ["..."],
  "user_faqs": ["..."],
  "decision_factors": ["..."],
  "brand_story": "...",
  "key_person_story": "...",
  "case_stories": ["..."],
  "brand_values": "...",
  "available_materials": ["..."],
  "brand_slogan": "...",
  "core_message": "...",
  "extra_notes": "..."
}
只输出一个 JSON 对象,不要加 ``` 围栏,不要写解释文本。
"""

SYSTEM_PROMPT = (
    "你是品牌画像分析师,把用户给的原始资料(公司简介 / 官网文案 / PRD / 产品说明 / "
    "市场材料等)抽取为结构化「品牌画像」JSON。\n" + _SCHEMA_HINT
)


def _resolve_provider() -> tuple[str | None, str, str]:
    """returns (provider, model_id, api_key); provider=None 表示不可用."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return "deepseek", DEEPSEEK_MODEL, ds_key
    if or_key:
        return "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    return None, "", ""


def extract_profile_from_text(text: str) -> tuple[BrandProfile, str]:
    provider, model_id, api_key = _resolve_provider()
    if not provider:
        return BrandProfile(), ""

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
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"以下是原始资料,请抽取为品牌画像 JSON:\n\n{text}"},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"unexpected {provider} response shape: {e}")
    parsed = _parse_json_loose(content)
    allowed = set(BrandProfile.model_fields.keys())
    safe = {k: v for k, v in parsed.items() if k in allowed and v is not None}
    try:
        profile = BrandProfile(**safe)
    except Exception as e:  # noqa: BLE001
        log.warning("BrandProfile 映射失败,降级用空画像: %s; raw=%s", e, content[:300])
        profile = BrandProfile()
    return profile, model_id


# ─────────────── 上传文件 → 纯文本 ─────────────────────────


# 上限同 BrandProfile.extra_notes 的合理边界:LLM 一次能消化的中文 ~30K 字
MAX_EXTRACTED_TEXT = 60_000

# 友好的错误消息体,调用方会包成 HTTPException
class FileParseError(Exception):
    pass


SUPPORTED_EXTS = ("txt", "md", "docx")


def file_bytes_to_text(filename: str, data: bytes) -> str:
    """根据后缀分发到对应解析器;返回截断到 MAX_EXTRACTED_TEXT 的文本。

    支持:
    - .docx → python-docx 段落 + 表格
    - .txt / .md → utf-8 / gbk 兜底

    其它类型(.pdf / .doc / .csv / .json …)统一 415,提示用户复制内容到文本框,
    或另存为支持的格式。PDF 一开始上过 pypdf,扫描件抽不到字反而误导用户,
    所以这里收紧到三种最稳的格式。
    """
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""

    if ext == "docx":
        return _truncate(_parse_docx(data))
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
    if ext == "pdf":
        raise FileParseError(
            "暂不支持 PDF。请复制内容粘贴到文本框,或另存为 .docx / .txt / .md 后上传"
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
    # 表格也抽一下 — PRD / 公司简介常用表格列字段
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
