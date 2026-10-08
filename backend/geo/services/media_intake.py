"""Pre-ingestion processing for user-uploaded assets (2026-09-16) -- trust only content, never the extension.

The upload endpoint used to look only at `Path(filename).suffix`, with mime copied from the browser-reported content_type.
Anything renamed to .png could get into the DB, only blowing up at labeling / external check / publishing, all of which swallow exceptions.
This brings three things together:

  sniff()      determine the real format by magic numbers / Pillow decoding; reject outright if it does not match the extension
  sanitize()   re-encode images once, stripping EXIF / XMP / tEXt -- GPS locations from phone originals
               must not go out with the public URL; also normalizes "extension png but actually jpg" along the way
  digest()     content sha256, for dedup within a topic

AI-generated images do not go through here (image_gen writes to disk directly), so their label metadata is not stripped.
"""
from __future__ import annotations

import hashlib
import io
import os
from typing import Optional

# ⚠️ PIL is only imported inside functions: api/ai_telemetry imports this module at top level, and the agent (:8010,
# separate agent-venv without Pillow) also imports ai_telemetry -- a top-level PIL import would stop the agent
# from starting (the 2026-09-16 test site deploy smoke test showing 8010 = 000 was exactly this)

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
ALLOWED_MEDIA_EXTS = ALLOWED_IMAGE_EXTS | ALLOWED_VIDEO_EXTS

MAX_MEDIA_BYTES = 50 * 1024 * 1024   # 50 MB per file, enough for video too
# Per-topic image count / total size caps. There were none before -- a user dragging in 200 images left disk and external check fees wide open
MAX_MEDIA_PER_TOPIC = int(os.environ.get("GEO_MEDIA_MAX_PER_TOPIC", "200"))
MAX_MEDIA_BYTES_PER_TOPIC = int(os.environ.get("GEO_MEDIA_MAX_BYTES_PER_TOPIC",
                                               str(2 * 1024 * 1024 * 1024)))

# Pillow format names <-> the extension families we accept; when normalizing, files are stored with the one in _CANON
_PIL_FAMILY = {
    "JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}, "GIF": {".gif"},
    "WEBP": {".webp"}, "BMP": {".bmp"},
}
_CANON = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp"}
_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska",
}


class MediaRejected(ValueError):
    """Content does not match the claimed format, or is not a type we accept. message is shown directly to the user."""


def _sniff_video(raw: bytes, ext: str) -> None:
    head = raw[:16]
    if ext in (".mp4", ".mov", ".m4v"):
        # ISO BMFF: the first 4 bytes are the box length, followed by 'ftyp'
        if head[4:8] != b"ftyp":
            raise MediaRejected(f"文件内容不是 {ext} 视频")
    elif ext in (".webm", ".mkv"):
        if head[:4] != b"\x1a\x45\xdf\xa3":          # EBML header
            raise MediaRejected(f"文件内容不是 {ext} 视频")


def sniff(raw: bytes, ext: str) -> tuple[str, str]:
    """-> (kind, mime). Extension not in the allowlist / content does not match the extension -> MediaRejected."""
    ext = (ext or "").lower()
    if ext in ALLOWED_VIDEO_EXTS:
        _sniff_video(raw, ext)
        return "video", _MIME_BY_EXT[ext]
    if ext not in ALLOWED_IMAGE_EXTS:
        raise MediaRejected(f"不支持的格式 {ext or '未知'}")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(raw)) as img:
            fmt = img.format or ""
            img.verify()            # Decode integrity; truncated / forged files fail here
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise MediaRejected(f"文件不是可解码的图片({ext})") from e
    family = _PIL_FAMILY.get(fmt)
    if not family:
        raise MediaRejected(f"不支持的图片编码 {fmt or '未知'}")
    if ext not in family:
        # Cases like extension .png with JPEG content -- do not guess what the user wants, normalize to the real format by content
        ext = _CANON[fmt]
    return "image", _MIME_BY_EXT[ext]


def sanitize(raw: bytes, ext: str) -> tuple[bytes, str]:
    """Strip metadata -> (new bytes, normalized extension).

    JPEG uses quality="keep" to keep the original quantization tables (near lossless); PNG is lossless; WEBP is only re-encoded (q=90)
    when it actually carries EXIF/XMP, otherwise left as-is; GIF (possibly animated) and BMP are left untouched.
    Any failing step falls back to the original bytes -- failing to strip metadata should not fail the upload; magic numbers were already checked in sniff.
    """
    ext = (ext or "").lower()
    if ext not in ALLOWED_IMAGE_EXTS or ext in (".gif", ".bmp"):
        return raw, ext
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as img:
            fmt = img.format or ""
            real_ext = ext if ext in _PIL_FAMILY.get(fmt, set()) else _CANON.get(fmt, ext)
            if fmt == "WEBP" and not (img.info.get("exif") or img.info.get("xmp")):
                return raw, real_ext
            img.load()
            buf = io.BytesIO()
            if fmt == "JPEG":
                img.save(buf, format="JPEG", quality="keep", optimize=True)
            elif fmt == "PNG":
                # Not passing pnginfo -> no tEXt/iTXt/eXIf at all
                img.save(buf, format="PNG", optimize=True)
            elif fmt == "WEBP":
                img.save(buf, format="WEBP", quality=90)
            else:
                return raw, real_ext
            return buf.getvalue(), real_ext
    except Exception:  # noqa: BLE001 -- see docstring
        return raw, ext


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def mime_for(ext: str) -> Optional[str]:
    return _MIME_BY_EXT.get((ext or "").lower())
