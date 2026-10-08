"""Qwen (Wanxiang) automatic article images -- image-to-image primarily, text-to-image as fallback.

After the article is written, pick one user source image by content; use it directly the first time, afterwards generate new images using it as reference.
The image prompt appends this article's topic after the fixed "fidelity + change camera position" requirements, steering the new image toward the article.
2026-09-29 -- when the asset library has no usable source image, or image-to-image conditions are unmet / it fails, fall back to text-to-image:
draw an illustration from the article title + summary (`/images/generations`), ensuring every article can have an image.
Results are stored under data/topic_media/{topic_id}/ and registered in TopicMediaORM, then follow the same
public access, body embedding and publish pipeline as user assets.

Image-to-image uses the existing OpenAI-compatible gateway's `/images/edits`: upload the user's source image as reference; the returned
temporary image URL must be downloaded to disk immediately and must not be written into the article directly.

2026-08-16 -- content labeling is enforced after generation (services/image_marking.py): visible watermark in the bottom-right
+ EXIF/tEXt metadata, items 7 and 8 of the "Measures for Labeling AI-Generated Synthetic Content". If labeling fails **the image is dropped**,
so no unlabeled AI-generated image enters the DB.

ENV:
    QWEN_IMAGE_GATEWAY_BASE            optional, falls back to DEEPSEEK_BASE_URL
    QWEN_IMAGE_GATEWAY_KEY             optional, falls back to DEEPSEEK_API_KEY
    QWEN_IMAGE_GATEWAY_MODEL           optional, default wan2.7-image
    GEO_IMAGE_GEN_TIMEOUT              optional, per-image generation timeout seconds, default 300
    QWEN_IMAGE_SIZE                    optional, output size, default 1536x1024 (landscape)
"""
from __future__ import annotations

import logging
import os
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests

from geo.models.ai_telemetry import TopicMediaORM
from geo.services import image_marking
from geo.services.llm_usage import record_llm_call

log = logging.getLogger(__name__)

_TIMEOUT = int(os.environ.get("GEO_IMAGE_GEN_TIMEOUT", "300"))
_SIZE = os.environ.get("QWEN_IMAGE_SIZE", "1536x1024")


# Image-to-image gateway: reuses the existing DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY by default, and can be
# overridden separately with QWEN_IMAGE_GATEWAY_*.
_GATEWAY_BASE = (os.environ.get("QWEN_IMAGE_GATEWAY_BASE")
                 or os.environ.get("DEEPSEEK_BASE_URL", "")).rstrip("/")
_GATEWAY_MODEL = os.environ.get("QWEN_IMAGE_GATEWAY_MODEL", "wan2.7-image")


def _gateway_key() -> str:
    return (os.environ.get("QWEN_IMAGE_GATEWAY_KEY")
            or os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def _use_gateway() -> bool:
    """Prefer the gateway when fully configured -- direct connection is broken, kept in config only for switching back in the future."""
    return bool(_GATEWAY_BASE and _gateway_key())


def available() -> bool:
    """Whether the image-to-image gateway is configured; not being configured does not count as a failure of any source image."""
    return _use_gateway()


def _active_model() -> str:
    """Model name actually producing the image -- stored in gen_model and in the image metadata's GenerationModel."""
    return _GATEWAY_MODEL


_IMAGE_EDIT_PROMPT = (
    "请以输入图片中的真实地点和内容为参考，生成同一地点的一张全新摄影图片。"
    "准确保留原图中的场所身份、建筑结构、品牌特征、主要设施和整体风格，"
    "不得虚构不存在的设施，不得改变店名、品牌或关键事实。"
    "新图片必须在以下三个方面与原图明显不同："
    "第一，改变拍摄场景，可调整时间、天气、光线、环境氛围或展示区域；"
    "第二，改变画面重点，选择原图中另一个真实存在的物品、设施或区域作为主体；"
    "第三，明显改变拍摄角度、距离和机位，例如左侧或右侧45度、近景、远景、"
    "高机位或低机位，避免复制原图构图。"
    "保持照片真实自然、透视合理、光影统一、细节清晰。"
    "如果无法确认某个物品或设施真实存在，则不要添加。"
    "不要添加无关人物、动物、错误文字、水印、边框或其他场所。"
)


_SUBJECT_MAX_CHARS = 200


def build_edit_prompt(subject: str = "") -> str:
    """Fixed fidelity requirements + this article's topic; falls back to the pure fixed prompt when the topic is empty.

    The topic only affects "which real area / what atmosphere" and must not override the authenticity constraints above --
    things written in the article but absent from the source image's location are better left undrawn.
    """
    subject = " ".join((subject or "").split())[:_SUBJECT_MAX_CHARS]
    if not subject:
        return _IMAGE_EDIT_PROMPT
    return (
        f"{_IMAGE_EDIT_PROMPT}"
        f"这张图将作为一篇文章的配图，文章主题是：「{subject}」。"
        "在不违背上述真实性要求的前提下，画面主体、场景和氛围应尽量贴合这个主题；"
        "主题中提到但原图地点不存在的事物不要画。"
    )


def _gateway_edit(source_path: Path, prompt: str = _IMAGE_EDIT_PROMPT) -> str:
    """Call `/images/edits` per the reference script, returning the generated image URL."""
    from PIL import Image

    image_file = BytesIO()
    with Image.open(source_path) as source_image:
        source_image.convert("RGB").save(image_file, "JPEG", quality=85)
    image_file.seek(0)
    resp = requests.post(
        f"{_GATEWAY_BASE}/images/edits",
        headers={"Authorization": f"Bearer {_gateway_key()}"},
        data={
            "model": _GATEWAY_MODEL,
            "prompt": prompt,
            "output_format": "png",
            "size": _SIZE,
        },
        files=[("image[]", ("input.jpg", image_file, "image/jpeg"))],
        timeout=_TIMEOUT,
    )
    data = resp.json() if resp.content else {}
    items = data.get("data") or []
    url = (items[0].get("url") or "").strip() if items and isinstance(items[0], dict) else ""
    if not resp.ok or not url:
        raise RuntimeError(f"网关图生图失败 HTTP {resp.status_code}: {str(data)[:300]}")
    record_llm_call(provider="gateway", model=_GATEWAY_MODEL, feature="image_gen",
                    call_kind="image", endpoint=_GATEWAY_BASE, status="ok",
                    http_status=200, extra={"unit": "image"})
    return url


# Chinese characters in generated images are basically garbled, so suppress text/watermarks uniformly; the gateway has no negative_prompt field, so it must go into the prompt
_NEGATIVE_PROMPT = "文字, 汉字, 字母, 水印, logo, 二维码, 低质量, 模糊, 变形"


def build_text_prompt(title: str, summary: str = "", industry: str = "") -> str:
    """Article title + summary -> text-to-image prompt.

    Illustration style rather than realistic photography: the fallback image has no real reference, and a "photo" would be taken by readers as a real brand shot.
    Describe a **concrete scene**, do not say "cover/header image" -- that induces the model to leave large blank areas for a title.
    """
    title = " ".join((title or "").split())[:120]
    summary = " ".join((summary or "").split())[:_SUBJECT_MAX_CHARS]
    scene = f"文章标题是「{title}」"
    if summary:
        scene += f"，内容概要：{summary}"
    if industry:
        scene += f"，行业背景为{industry.strip()[:40]}"
    return (
        f"为一篇文章绘制配图。{scene}。"
        "请根据文章主题画出一个具体、有叙事感的场景，直观体现文章在讲的事情，"
        "现代扁平插画风格，色彩协调明亮，横版构图，细节丰富铺满整个画面；"
        "人物用简约插画风(非写实人脸)，不要画出真实品牌标识或店名。"
        f"画面中不要出现:{_NEGATIVE_PROMPT}"
    )


def _gateway_generate(prompt: str) -> str:
    """Synchronous text-to-image via the OpenAI-compatible gateway `/images/generations`, returning the image URL."""
    resp = requests.post(
        f"{_GATEWAY_BASE}/images/generations",
        headers={"Authorization": f"Bearer {_gateway_key()}",
                 "Content-Type": "application/json"},
        json={"model": _GATEWAY_MODEL, "prompt": prompt, "size": _SIZE, "n": 1},
        timeout=_TIMEOUT,
    )
    data = resp.json() if resp.content else {}
    items = data.get("data") or []
    url = (items[0].get("url") or "").strip() if items and isinstance(items[0], dict) else ""
    if not resp.ok or not url:
        raise RuntimeError(f"网关文生图失败 HTTP {resp.status_code}: {str(data)[:300]}")
    record_llm_call(provider="gateway", model=_GATEWAY_MODEL, feature="image_gen",
                    call_kind="image", endpoint=_GATEWAY_BASE, status="ok",
                    http_status=200, extra={"unit": "image", "mode": "t2i"})
    return url


def _download_to_media(url: str, topic_id: int) -> tuple[Path, int, str]:
    """Download the temporary OSS image -> data/topic_media/{topic_id}/{32hex}.{ext}.

    The filename must satisfy the public endpoint's `[a-f0-9]{32}\\.[a-zA-Z0-9]{2,5}` validation.
    Returns (disk path, byte count, ext).
    """
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    raw = resp.content
    if not raw:
        raise RuntimeError("万相图片下载为空")
    ext = ".png"
    from geo.services import media_storage
    stored_path = media_storage.store_bytes(topic_id, f"{uuid.uuid4().hex}{ext}", raw)
    return stored_path, len(raw), ext


def _store_generated(db, *, topic_id: int, user_id: int, stored_path: Path, size: int,
                     ext: str, filename: str,
                     source_media_id: Optional[int]) -> Optional[TopicMediaORM]:
    """Generated image on disk -> register a pending asset row + content labeling + external check; returns None and drops the image if labeling fails."""
    from geo.services import media_storage
    row = TopicMediaORM(
        topic_id=topic_id, user_id=user_id,
        filename=filename,
        kind="image",
        mime="image/webp" if ext == ".webp" else f"image/{ext.lstrip('.').replace('jpg', 'jpeg')}",
        size=size, storage_path=str(stored_path),
        is_ai_generated=True, gen_model=_active_model(),
        source_media_id=source_media_id, generation_state="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Content labeling hook ("Labeling Measures" item 7): EXIF/tEXt implicit label.
    # Visible watermark is off by default since 2026-08-17 (images go to external media with the article, and burned-in text would be published along with them);
    # set GEO_IMAGE_WATERMARK=1 when producing filing evidence images; see the services/image_marking module header.
    # Must come after commit -- the image content id includes media.id.
    # **Drop the image if labeling fails**: better for this article to have no image than let AI images without provenance metadata go external.
    if not image_marking.stamp_media(db, row, model=_active_model()):
        log.error("image marking failed, dropping the image topic=%d media_id=%d", topic_id, row.id)
        db.delete(row)
        db.commit()
        media_storage.delete_file(topic_id, str(stored_path))
        return None
    db.commit()
    db.refresh(row)

    # External check soft signal hook (2026-08-18, image side of items 9/10 in the correction list): what is sent must be the **final labeled image on disk**.
    # Never blocks -- external check failure/degradation only leaves a trace, and the image is stored as usual.
    try:
        from geo.services.compliance_scanner import external_check_media
        external_check_media(db, row)
        db.commit()
    except Exception:  # noqa: BLE001
        log.warning("image external scan skipped media=%d", row.id, exc_info=True)

    return row


def generate_article_image(
    db, *, topic_id: int, user_id: int, source_media: TopicMediaORM,
    subject: str = "",
) -> Optional[TopicMediaORM]:
    """Generate 1 image from the user's source image and add it to the asset library.

    subject: this article's topic (title + summary), appended to the image prompt; if empty, only the camera position of the source is changed.

    Success -> returns the committed TopicMediaORM (same table and pipeline as user-uploaded assets);
    no key configured / generation failed -> returns None (images are an enhancement; the caller does not fail because of it).
    Blocks synchronously for 10-60s; only call from a background thread / an already long-running regeneration request.
    """
    if not _use_gateway():
        log.info("image edit skipped: QWEN_IMAGE_GATEWAY_BASE/KEY or DEEPSEEK fallback not configured")
        return None
    if source_media is None:
        log.warning("image edit skipped: source row missing")
        return None
    from geo.services import media_storage
    source_path = media_storage.media_file(source_media)
    if source_path is None or not source_path.is_file():
        log.warning("image edit skipped: source missing media=%s", source_media.id)
        return None
    try:
        img_url = _gateway_edit(source_path, build_edit_prompt(subject))
        stored_path, size, ext = _download_to_media(img_url, topic_id)
    except Exception as e:  # noqa: BLE001
        log.warning("image edit failed topic=%d source=%s: %s", topic_id, source_media.id, e)
        return None

    source_name = Path(source_media.filename or f"media-{source_media.id}").stem
    row = _store_generated(
        db, topic_id=topic_id, user_id=user_id, stored_path=stored_path, size=size,
        ext=ext, filename=f"AI配图-{source_name[:240]}{ext}",
        source_media_id=source_media.id)
    if row is None:
        return None
    log.info("image edit ok topic=%d source=%d media_id=%d trace=%s (%d KB)",
             topic_id, source_media.id, row.id, row.gen_trace_no, row.size // 1024)
    return row


def generate_text_image(
    db, *, topic_id: int, user_id: int, title: str, summary: str = "",
    industry: str = "",
) -> Optional[TopicMediaORM]:
    """Text-to-image fallback: draw 1 illustration from the article title + summary and add it to the asset library (pending, attached to the draft by the caller).

    Same contract as generate_article_image: returns the committed row on success, None on failure / not configured.
    """
    if not _use_gateway():
        log.info("text image skipped: QWEN_IMAGE_GATEWAY_BASE/KEY or DEEPSEEK fallback not configured")
        return None
    title = (title or "").strip()
    if not title:
        return None
    try:
        img_url = _gateway_generate(build_text_prompt(title, summary, industry))
        stored_path, size, ext = _download_to_media(img_url, topic_id)
    except Exception as e:  # noqa: BLE001
        log.warning("text image failed topic=%d title=%r: %s", topic_id, title, e)
        return None
    row = _store_generated(
        db, topic_id=topic_id, user_id=user_id, stored_path=stored_path, size=size,
        ext=ext, filename=f"AI配图-{title[:200]}{ext}", source_media_id=None)
    if row is None:
        return None
    log.info("text image ok topic=%d media_id=%d trace=%s (%d KB)",
             topic_id, row.id, row.gen_trace_no, row.size // 1024)
    return row
