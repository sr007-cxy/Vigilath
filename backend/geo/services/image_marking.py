"""Image content labeling -- image-side implementation of the "Measures for Labeling AI-Generated Synthetic Content".

Corresponds to items 8 (explicit image labeling) and 7 (implicit image labeling) of the algorithm filing correction list;
plan in `docs/09-软著与专利/备案补正/02-方案与代办台账.md` sections 4.3 / 4.4.
The text-side counterpart is `content_marking.py`; both share the filing entity metric definition.

Two things done at once (each image is decoded/encoded only once):

1. **Explicit label (visible watermark)** -- ⚠️ **off by default** since 2026-08-17 (`GEO_IMAGE_WATERMARK=0`).
   AI images go to external media with the article via publish channels, and "内容由AI生成,仅供参考" ("AI-generated content, for reference only") burned into the image
   would be published by the media along with it. Set `GEO_IMAGE_WATERMARK=1` temporarily when producing filing evidence images;
   the `draw_watermark()` implementation is kept as-is.
2. **Implicit label** -- written into file metadata: PNG uses `tEXt` chunks, JPEG / WebP use EXIF.
   Five payload fields: full filing entity name, filing number, generation model, content id, generation time.
   **Always written** -- metadata is not shown on the page and does not affect media presentation; it is the actual handle for provenance.

Image content ids use the `VGI` prefix (text drafts use `VGL`); they are different tables with different primary keys,
and sharing a prefix would collide on reverse lookup.

⚠️ **Chinese fonts are a hard dependency**. Without CJK fonts installed on the server, Pillow renders "内容由AI生成" ("AI-generated content")
as a row of boxes -- worse than no label (submitting such a screenshot proves our labeling is non-compliant). So when no
CJK font is found, degrade to ASCII copy and log an ERROR, never draw boxes. For deployment see
the fonts-noto-cjk prerequisite in `.claude/deploy-prod.sh`.
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from geo.services.content_marking import DISCLOSURE_TEXT, SUBJECT_CODE, SUBJECT_NAME

log = logging.getLogger(__name__)

# Image content id prefix -- distinct from text drafts' VGL; reverse lookups route to different tables by prefix
IMAGE_TRACE_PREFIX = "VGI"

# Explicit label copy -- reuses the text-side sentence directly, word for word on both sides (reviewers compare them side by side).
# ⚠️ Ledger section 4.3 originally said "bottom-right + short phrase '内容由AI生成' ("AI-generated content")", but the reviewer's sample image
# (03-清单示例图/08-图片显式标识-示例.png) shows **bottom-center + full sentence**;
# the sample image is authoritative.
WATERMARK_TEXT = DISCLOSURE_TEXT
# Fallback copy when no CJK font is available (pure ASCII, renderable in any font)
WATERMARK_TEXT_ASCII = "AI-GENERATED CONTENT, FOR REFERENCE ONLY"

# Visible watermark master switch -- off by default since 2026-08-17, see the module header for why. Metadata is unaffected by this switch.
WATERMARK_ENABLED = os.environ.get(
    "GEO_IMAGE_WATERMARK", "0").strip().lower() in ("1", "true", "yes", "on")

# Algorithm filing number -- backfilled after approval (see "算法备案编号" ("algorithm filing number") in 00-总览.md).
# When empty, metadata says "备案中" ("filing in progress") rather than leaving the field empty: reviewers would treat an empty field as not integrated.
FILING_NO = os.environ.get("GEO_ALGO_FILING_NO", "").strip()

# Watermark font size: 3.5% of image height (the "Labeling Measures" only say "clearly discernible"; 2% is the floor set in the ledger,
# but the ratio in the sample image is closer to 3.5%, and erring on the larger side is safe). An absolute floor prevents blur on small images.
_FONT_RATIO = float(os.environ.get("GEO_WATERMARK_FONT_RATIO", "0.035"))
_FONT_MIN_PX = 18
# Watermark position: bottom-center (matches the sample image) / bottom-right (the ledger's original plan, kept switchable)
_POSITION = os.environ.get("GEO_WATERMARK_POSITION", "bottom-center").strip()

# Font lookup order: env-specified > common CJK font paths > live fc-list scan
_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)

_font_path_cache: Optional[str] = None
_font_missing_logged = False


# --------------------------- Content id ---------------------------
def make_image_trace_no(media_id: int, created_at: Optional[datetime] = None) -> str:
    """`VGI-<entity code>-<yyyymmdd>-<six-digit media.id>`, same metric definition as text draft ids (Beijing date)."""
    base = created_at or datetime.utcnow()
    day = (base + timedelta(hours=8)).strftime("%Y%m%d")
    return f"{IMAGE_TRACE_PREFIX}-{SUBJECT_CODE}-{day}-{int(media_id):06d}"


def is_image_trace_no(trace_no: str) -> bool:
    return (trace_no or "").strip().upper().startswith(IMAGE_TRACE_PREFIX + "-")


# --------------------------- Fonts ---------------------------
def _find_cjk_font() -> Optional[str]:
    """Locate a font file that can render Chinese; returns None if not found (the caller degrades to ASCII copy)."""
    global _font_path_cache
    if _font_path_cache is not None:
        return _font_path_cache or None

    env = os.environ.get("GEO_WATERMARK_FONT", "").strip()
    cands = ([env] if env else []) + list(_FONT_CANDIDATES)
    for p in cands:
        if p and Path(p).is_file():
            _font_path_cache = p
            return p

    # Fallback: scan the system live for fonts with Chinese glyphs. Use fc-list instead of fc-match --
    # on machines missing fonts, fc-match "sans-serif:lang=zh" returns DejaVuSans without Chinese
    # instead of an error, and drawing with it yields a screen full of boxes.
    try:
        out = subprocess.run(["fc-list", ":lang=zh", "file"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            f = line.split(":", 1)[0].strip()
            if f.lower().endswith((".ttf", ".ttc", ".otf")) and Path(f).is_file():
                _font_path_cache = f
                return f
    except Exception:  # noqa: BLE001 -- missing fc-list should not break the image pipeline
        pass

    _font_path_cache = ""
    return None


def _load_font(px: int) -> tuple[ImageFont.FreeTypeFont, str]:
    """Returns (font, watermark copy this font can render)."""
    global _font_missing_logged
    path = _find_cjk_font()
    if path:
        try:
            return ImageFont.truetype(path, px), WATERMARK_TEXT
        except Exception as e:  # noqa: BLE001
            log.warning("failed to load watermark font %s: %s", path, e)
    if not _font_missing_logged:
        _font_missing_logged = True
        log.error("server has no Chinese font, image watermark degraded to ASCII copy. "
                  "Install fonts-noto-cjk and restart, otherwise item 8 evidence is non-compliant")
    # Fall back to DejaVu (present on almost every Linux); only then use Pillow's built-in bitmap font
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, px), WATERMARK_TEXT_ASCII
            except Exception:  # noqa: BLE001
                pass
    return ImageFont.load_default(), WATERMARK_TEXT_ASCII


# --------------------------- Explicit label ---------------------------
def draw_watermark(img: Image.Image) -> Image.Image:
    """Draw a semi-transparent black band + white label text at the bottom. Returns a new image (input unchanged).

    Uses RGBA layer compositing instead of drawing directly -- direct drawing cannot produce a semi-transparent band. The sample image has a light beach
    with gray text, while our images are dark business illustrations where gray text simply disappears, so we uniformly add a dark band +
    white text: readable on any background, compliance only gets stronger.
    """
    base = img.convert("RGBA")
    w, h = base.size
    px = max(_FONT_MIN_PX, int(h * _FONT_RATIO))
    font, text = _load_font(px)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    pad_x, pad_y = max(8, px // 2), max(5, px // 3)
    margin = max(10, px // 2)
    box_w, box_h = tw + pad_x * 2, th + pad_y * 2

    if _POSITION == "bottom-right":
        x0 = w - box_w - margin
    else:  # bottom-center -- matches the reviewer's sample image, default
        x0 = (w - box_w) // 2
    y0 = h - box_h - margin
    # On extremely small images negative coordinates would push the watermark off the canvas; clamp to 0
    x0, y0 = max(0, x0), max(0, y0)

    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h],
                           radius=max(4, px // 4), fill=(0, 0, 0, 150))
    draw.text((x0 + pad_x - l, y0 + pad_y - t), text, font=font, fill=(255, 255, 255, 240))
    return Image.alpha_composite(base, layer)


# --------------------------- Implicit label ---------------------------
def build_metadata(*, trace_no: str, model: str,
                   created_at: Optional[datetime] = None) -> dict[str, str]:
    """Metadata payload -- the national-standard `AIGC` field + a set of flat readable supplementary keys.

    ⚠️ Fixed 2026-08-18: the value of `AIGC` used to be just the string `"true"`, which did not match the reviewer's sample image
    (`03-清单示例图/07-图像隐式标识-示例.png`) -- that sample shows the JSON structure of
    GB 45438, the national standard accompanying the "Labeling Measures", with seven fixed fields:
    `Label` / `ContentProducer` / `ProduceID` / `ReservedCode1` /
    `ContentPropagator` / `PropagateID` / `ReservedCode2`. If field names do not match,
    reviewers comparing field by field against the image will judge it non-standard. Now writes the national-standard JSON.

    Mapping metric definition: `Label="1"` means AI-generated synthetic; `ContentProducer` is the generation service provider
    (our full filing entity name); `ProduceID` uses our content id `VGI-...`; `ReservedCode1`
    holds the algorithm filing number ("备案中" ("filing in progress") before approval). `ContentPropagator` / `PropagateID` are
    the slots for the **propagation service provider** -- we only generate, not propagate, so leaving them empty is correct; after publish channels
    post to external media, the receiving platform fills them.

    The other flat keys (ContentProducer / GenerationModel / ...) are kept: the national-standard JSON squeezed into one
    field reads poorly, and flat keys let exiftool and the system properties panel show the entity name at a glance.
    """
    base = created_at or datetime.utcnow()
    gen_time = (base + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S +0800")
    aigc = json.dumps({
        "Label": "1",
        "ContentProducer": SUBJECT_NAME,
        "ProduceID": trace_no,
        "ReservedCode1": FILING_NO or "备案中",
        "ContentPropagator": "",
        "PropagateID": "",
        "ReservedCode2": "",
    }, ensure_ascii=False, separators=(",", ":"))
    return {
        "AIGC": aigc,
        "ContentProducer": SUBJECT_NAME,
        "ProducerFilingNo": FILING_NO or "备案中",
        "GenerationModel": model or "",
        "ContentTraceNo": trace_no,
        "GenerationTime": gen_time,
        # Whether a visible watermark was drawn on this image at the time -- the watermark switch is toggled as evidence needs change,
        # and without recording it we could never tell later whether a given existing image carries an explicit label (the asset library column needs it).
        "VisibleWatermark": "on" if WATERMARK_ENABLED else "off",
    }


def _metadata_text(meta: dict[str, str]) -> str:
    """Collapse into one line -- to fit single-value fields like EXIF ImageDescription."""
    return "; ".join(f"{k}={v}" for k, v in meta.items() if v)


def _encode(img: Image.Image, ext: str, meta: dict[str, str]) -> bytes:
    """Encode by extension and write metadata. PNG -> tEXt; JPEG / WebP -> EXIF."""
    buf = io.BytesIO()
    ext = (ext or ".png").lower()
    line = _metadata_text(meta)

    if ext == ".png":
        info = PngInfo()
        for k, v in meta.items():
            if v:
                info.add_text(k, v)
        # Generic viewers mostly only recognize Description / Software, so also write a combined string separately
        info.add_text("Description", line)
        info.add_text("Software", f"{SUBJECT_NAME} / {meta.get('GenerationModel', '')}")
        img.convert("RGB").save(buf, format="PNG", pnginfo=info, optimize=True)
        return buf.getvalue()

    # ⚠️ EXIF ImageDescription / Software / Artist / Copyright are ASCII by spec;
    # passing a str makes Pillow encode as ASCII, and Chinese names like "<company-name>" become a string of
    # `?????????????` -- which is exactly how the evidence screenshot looks. So Chinese is always **written as bytes**
    # (UTF-8 stored as-is, readable by exiftool / modern viewers), plus two more fields with explicit encoding declarations
    # as fallback: XP* (UCS-2LE, natively supported by the Windows properties panel) + UserComment (UTF-16).
    exif = Image.Exif()
    software = f"{SUBJECT_NAME} / {meta.get('GenerationModel', '')}"
    exif[0x010E] = line.encode("utf-8")                    # ImageDescription
    exif[0x0131] = software.encode("utf-8")                # Software
    exif[0x013B] = SUBJECT_NAME.encode("utf-8")            # Artist
    exif[0x8298] = SUBJECT_NAME.encode("utf-8")            # Copyright
    exif[0x9C9C] = line.encode("utf-16-le")                # XPComment
    exif[0x9C9D] = SUBJECT_NAME.encode("utf-16-le")        # XPAuthor
    exif[0x9C9F] = WATERMARK_TEXT.encode("utf-16-le")      # XPSubject
    try:
        # UserComment lives in the Exif sub-IFD; the first 8 bytes are the encoding marker (UNICODE here)
        exif.get_ifd(0x8769)[0x9286] = b"UNICODE\0" + line.encode("utf-16-le")
    except Exception:  # noqa: BLE001 -- failing to write the sub-IFD does not affect the main fields
        pass

    if ext == ".webp":
        img.convert("RGB").save(buf, format="WEBP", quality=90, exif=exif)
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=92, exif=exif)
    return buf.getvalue()


# --------------------------- Public entry points ---------------------------
def apply_marks(raw: bytes, ext: str, *, trace_no: str, model: str = "",
                created_at: Optional[datetime] = None,
                watermark: Optional[bool] = None) -> bytes:
    """Apply the implicit label in one pass (+ draw a visible watermark depending on the switch), returning new image bytes.

    `watermark=None` follows the global switch `WATERMARK_ENABLED` (off by default, see module header);
    pass `watermark=True` explicitly when producing filing evidence images.

    All failures are raised for the caller to decide -- images are an enhancement, but **AI images whose metadata cannot be written
    must not enter the DB**; that decision is left to stamp_media().
    """
    img = Image.open(io.BytesIO(raw))
    img.load()
    meta = build_metadata(trace_no=trace_no, model=model, created_at=created_at)
    draw = WATERMARK_ENABLED if watermark is None else bool(watermark)
    return _encode(draw_watermark(img) if draw else img, ext, meta)


def _decode_exif_text(v) -> str:
    """Decode EXIF text fields.

    We write UTF-8 bytes, and Pillow reads them back decoded as latin-1 into garbage
    (`ä¸æµ·...`). Here we first restore the raw bytes via latin-1, then try UTF-8 -- if that fails
    it was plain ASCII to begin with, so return as-is.
    """
    if isinstance(v, bytes):
        raw = v
    elif isinstance(v, str):
        try:
            raw = v.encode("latin-1")
        except UnicodeEncodeError:
            return v
    else:
        return str(v)
    for enc in ("utf-8", "utf-16-le", "latin-1"):
        try:
            s = raw.decode(enc)
            if enc == "utf-16-le":
                s = s.rstrip("\x00")
            return s
        except UnicodeDecodeError:
            continue
    return str(v)


def read_marks(path: str | Path) -> dict[str, str]:
    """Read back the implicit label from an image -- for provenance self-check and evidence review (file unchanged)."""
    out: dict[str, str] = {}
    try:
        with Image.open(path) as img:
            # PNG: tEXt chunks, keys are exactly build_metadata's six fields
            for k, v in (getattr(img, "text", None) or {}).items():
                out[str(k)] = str(v)
            exif = img.getexif()
            if exif:
                for tag, name in ((0x010E, "Description"), (0x0131, "Software"),
                                  (0x013B, "Artist"), (0x8298, "Copyright"),
                                  (0x9C9C, "XPComment"), (0x9C9D, "XPAuthor")):
                    v = exif.get(tag)
                    if v:
                        out.setdefault(name, _decode_exif_text(v))
                # JPEG / WebP have no tEXt; the six fields can only be split back out of the combined string, for the upper layer to read by field
                merged = out.get("XPComment") or out.get("Description") or ""
                for part in merged.split("; "):
                    if "=" in part:
                        k, _, v = part.partition("=")
                        out.setdefault(k.strip(), v.strip())
    except Exception as e:  # noqa: BLE001
        log.warning("failed to read image metadata %s: %s", path, e)
    return out


# EXIF field names that read_marks brings along -- they are containers _encode uses for the combined string,
# not build_metadata's six fields, and must not be written again as keys when converting formats
_EXIF_CONTAINER_KEYS = frozenset(
    ("Description", "Software", "Artist", "Copyright", "XPComment", "XPAuthor"))


def transcode(path: str | Path, ext: str) -> bytes:
    """Convert to another image format, **carrying the implicit label along**. Returns bytes in the new format, original file untouched.

    Used for format conversion before going out (2026-09-16): Meijieku's UEditor only accepts png/jpg/gif/bmp, while the image gateway
    returns webp. A plain `Image.save` would drop the content id in EXIF/tEXt -- an AI image
    becoming "unlabeled" just by changing its extension and flowing externally is unacceptable. The visible watermark is in the pixels and is naturally kept.

    Original image has no label (user upload) -> only convert the format, never invent an AI label.
    """
    meta = {k: v for k, v in read_marks(path).items()
            if v and k not in _EXIF_CONTAINER_KEYS}
    with Image.open(path) as img:
        img.load()
        if not meta:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format=_PIL_FORMAT.get(ext.lower(), "PNG"))
            return buf.getvalue()
        return _encode(img, ext, meta)


_PIL_FORMAT = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


def stamp_media(db, media, *, model: str = "") -> bool:
    """Label an AI image already in the DB -- persist the id + rewrite the file in place. Returns whether anything changed.

    **Must be called after media is committed and has a primary key** (the id includes media.id).
    Idempotent: rows that already have gen_trace_no are skipped, so the watermark is never stacked twice.

    On labeling failure returns False and logs ERROR: the caller (image_gen) then **drops this image** --
    better for this article to have no image than let an unlabeled AI image flow externally.
    """
    if getattr(media, "gen_trace_no", None):
        return False
    from geo.services import media_storage
    path = media_storage.media_file(media)
    if path is None or not path.is_file():
        log.error("image labeling failed: file not found media=%s path=%s", media.id, path or media.storage_path)
        return False
    try:
        trace_no = make_image_trace_no(media.id, getattr(media, "uploaded_at", None))
        marked = apply_marks(path.read_bytes(), path.suffix,
                             trace_no=trace_no, model=model,
                             created_at=getattr(media, "uploaded_at", None))
        path.write_bytes(marked)
        media_storage.sync_file(media.topic_id, path.name)     # The copy in shared storage must also be labeled
        media.gen_trace_no = trace_no
        media.gen_model = model or ""
        media.is_ai_generated = True
        media.size = len(marked)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("image labeling failed media=%s: %s", getattr(media, "id", "?"), e)
        return False
