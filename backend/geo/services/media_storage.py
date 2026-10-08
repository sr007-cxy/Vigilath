"""On-disk location of asset files -- the single convention (2026-09-16).

Previously `Path(os.environ.get("GEO_DATA_DIR") or "./data") / "topic_media"` was copied separately into
api/ai_telemetry, services/image_gen, ruanwenjiang_publisher and aitoearn_publisher, while `topic_media.storage_path` stored
paths like `data/topic_media/{tid}/{uuid}.{ext}`,
**relative to the process cwd**. Consequence: starting a process with a different cwd (cron, agent :8010) could not find
files; and once `GEO_DATA_DIR` pointed elsewhere, old rows' storage_path no longer matched the real location.

This unifies it: **files are always located at `media_root()/{topic_id}/{basename}`**; storage_path
is only "the source of the filename", never opened directly as a path.

-- Shared storage (S3 / MinIO) ------------------------------------------------
The test site (test host) and production (prod) share one appdb but each has its own local disk: an image uploaded on one side
has a DB row on the other side but no file on disk. With `GEO_MEDIA_BACKEND=s3` the local disk becomes a **write-through cache**:
  - Write: store locally first, then put to the bucket (put failure raises; the DB must not gain a row whose image nobody can get)
  - Read: if not local, pull from the bucket then return a Path -- callers still get a local path,
        so labeling / transcoding / publish image reads / FileResponse need no change
  - After in-place rewrite (stamp_media labeling) call sync_file() to push the new bytes back
  - Delete: delete locally + in the bucket together
The bucket need not be public: externally it is still the /api/ai-telemetry/public/topic-media/ endpoint, which the backend fetches and serves,
so Alibaba Cloud external check fetches, URLs embedded in drafts and image reads before Meijieku upload are all unaware the backend changed.

Environment variables: GEO_MEDIA_BACKEND=s3 GEO_S3_ENDPOINT GEO_S3_BUCKET GEO_S3_ACCESS_KEY
GEO_S3_SECRET_KEY [GEO_S3_REGION=us-east-1] [GEO_S3_PREFIX=topic_media]
To load existing files: scripts/media_sync_to_s3.py (run once on each of the two machines, idempotent).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

MEDIA_DIR_NAME = "topic_media"
PUBLIC_PREFIX = "/api/ai-telemetry/public/topic-media"

# The public endpoint only accepts filenames of this shape (32 hex uuid + extension); files are named this way when stored
_SAFE_NAME = re.compile(r"[a-f0-9]{32}\.[a-zA-Z0-9]{2,5}")
_PUBLIC_URL_RE = re.compile(
    re.escape(PUBLIC_PREFIX) + r"/(\d+)/([a-f0-9]{32}\.[a-zA-Z0-9]{2,5})$")


# --------------------------- Local disk ---------------------------
def data_dir() -> Path:
    """`GEO_DATA_DIR`, default ./data (same convention as the DATABASE_URL default sqlite:///./data/...)."""
    return Path(os.environ.get("GEO_DATA_DIR") or "./data")


def media_root() -> Path:
    root = data_dir() / MEDIA_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def topic_dir(topic_id: int) -> Path:
    d = media_root() / str(int(topic_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _local_path(topic_id: int, name: str) -> Path:
    return media_root() / str(int(topic_id)) / name


def _safe_name(storage_path_or_name: str) -> Optional[str]:
    name = Path(storage_path_or_name or "").name
    return name if _SAFE_NAME.fullmatch(name) else None


# ─────────────────────────── S3 / MinIO ───────────────────────────
def backend() -> str:
    return (os.environ.get("GEO_MEDIA_BACKEND") or "local").strip().lower()


def s3_enabled() -> bool:
    return backend() == "s3"


_s3_lock = threading.Lock()
_s3_client = None
_s3_bucket = ""
_s3_prefix = ""


def _s3():
    """Lazily create the client (boto3 is only imported for the s3 backend; local development need not install it)."""
    global _s3_client, _s3_bucket, _s3_prefix
    if _s3_client is not None:
        return _s3_client
    with _s3_lock:
        if _s3_client is not None:
            return _s3_client
        import boto3
        from botocore.config import Config
        endpoint = (os.environ.get("GEO_S3_ENDPOINT") or "").rstrip("/")
        bucket = os.environ.get("GEO_S3_BUCKET") or ""
        if not endpoint or not bucket:
            raise RuntimeError("GEO_MEDIA_BACKEND=s3 但 GEO_S3_ENDPOINT / GEO_S3_BUCKET 未配")
        _s3_bucket = bucket
        _s3_prefix = (os.environ.get("GEO_S3_PREFIX") or MEDIA_DIR_NAME).strip("/")
        _s3_client = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=os.environ.get("GEO_S3_ACCESS_KEY") or None,
            aws_secret_access_key=os.environ.get("GEO_S3_SECRET_KEY") or None,
            region_name=os.environ.get("GEO_S3_REGION") or "us-east-1",
            # MinIO: v4 signing + path-style (virtual-host style needs wildcard DNS, which MinIO lacks by default)
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
        )
        return _s3_client


def _key(topic_id: int, name: str) -> str:
    return f"{_s3_prefix or MEDIA_DIR_NAME}/{int(topic_id)}/{name}"


def _s3_put(topic_id: int, name: str, data: bytes) -> None:
    import mimetypes
    ct = mimetypes.guess_type(name)[0] or "application/octet-stream"
    _s3().put_object(Bucket=_s3_bucket, Key=_key(topic_id, name), Body=data, ContentType=ct)


def _s3_fetch(topic_id: int, name: str, dest: Path) -> bool:
    """bucket -> local. Returns False if the object does not exist; network errors are raised (the caller decides whether to swallow)."""
    from botocore.exceptions import ClientError
    try:
        obj = _s3().get_object(Bucket=_s3_bucket, Key=_key(topic_id, name))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
            return False
        raise
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in obj["Body"].iter_chunks(1024 * 256):
            f.write(chunk)
    os.replace(tmp, dest)          # Atomic placement: another request pulling at the same time never reads half a file
    return True


def _s3_exists(topic_id: int, name: str) -> bool:
    from botocore.exceptions import ClientError
    try:
        _s3().head_object(Bucket=_s3_bucket, Key=_key(topic_id, name))
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _s3_delete(topic_id: int, name: str) -> None:
    _s3().delete_object(Bucket=_s3_bucket, Key=_key(topic_id, name))


def _s3_delete_prefix(topic_id: int) -> int:
    c = _s3()
    prefix = f"{_s3_prefix or MEDIA_DIR_NAME}/{int(topic_id)}/"
    n = 0
    token = None
    while True:
        kw = {"Bucket": _s3_bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = c.list_objects_v2(**kw)
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            c.delete_objects(Bucket=_s3_bucket, Delete={"Objects": keys, "Quiet": True})
            n += len(keys)
        if not page.get("IsTruncated"):
            return n
        token = page.get("NextContinuationToken")


# --------------------------- Public interface ---------------------------
def store_bytes(topic_id: int, name: str, data: bytes) -> Path:
    """Store a new file (local + bucket). Returns the local path -- just store str(it) as storage_path.

    A bucket put failure **raises**: an extra DB row whose file nobody can get is worse than a failed upload.
    """
    if not _safe_name(name):
        raise ValueError(f"非法素材文件名 {name!r}")
    p = topic_dir(topic_id) / name
    p.write_bytes(data)
    if s3_enabled():
        try:
            _s3_put(topic_id, name, data)
        except Exception:
            p.unlink(missing_ok=True)
            raise
    return p


def sync_file(topic_id: int, name: str) -> None:
    """Push back to the bucket after the local file is rewritten in place (labeling). No-op for the local backend."""
    if not s3_enabled():
        return
    p = _local_path(topic_id, name)
    if p.is_file():
        _s3_put(topic_id, name, p.read_bytes())


def fetch(topic_id: int, name: str) -> Optional[Path]:
    """(topic_id, filename) -> local path, pulled from the bucket if not local. Returns None if unavailable."""
    safe = _safe_name(name)
    if not safe:
        return None
    p = _local_path(topic_id, safe)
    if p.is_file():
        return p
    if s3_enabled():
        try:
            if _s3_fetch(topic_id, safe, p):
                return p
        except Exception:  # noqa: BLE001 -- bucket hiccups are treated as "temporarily unavailable"; the caller returns 404/skips
            log.warning("s3 fetch failed topic=%s name=%s", topic_id, safe, exc_info=True)
    return None


def file_for(topic_id: int, storage_path: str) -> Optional[Path]:
    """(topic_id, storage_path) -> file path on this machine. Returns None for invalid filenames.

    **Does not check existence or pull from the bucket** -- also used to compute the location before writing. Use media_file() to read files.
    """
    name = _safe_name(storage_path)
    return _local_path(topic_id, name) if name else None


def media_file(media) -> Optional[Path]:
    """TopicMediaORM row -> file path on this machine. With the s3 backend, pulls first if not local; the only case returning None
    is an invalid filename -- when the file really does not exist the path is still returned (consistent with file_for), and callers check is_file() themselves.
    """
    name = _safe_name(media.storage_path or "")
    if not name:
        return None
    p = _local_path(media.topic_id, name)
    if not p.is_file() and s3_enabled():
        fetch(media.topic_id, name)
    return p


def file_present(media) -> bool:
    """Whether the file is available. One local stat; with the s3 backend, if not local, one head on the bucket (no download)."""
    name = _safe_name(media.storage_path or "")
    if not name:
        return False
    if _local_path(media.topic_id, name).is_file():
        return True
    if s3_enabled():
        try:
            return _s3_exists(media.topic_id, name)
        except Exception:  # noqa: BLE001
            log.warning("s3 head failed topic=%s name=%s", media.topic_id, name, exc_info=True)
    return False


def delete_file(topic_id: int, storage_path_or_name: str) -> None:
    """Delete a file (local + bucket). No error if it does not exist."""
    name = _safe_name(storage_path_or_name)
    if not name:
        return
    try:
        _local_path(topic_id, name).unlink(missing_ok=True)
    except OSError as e:
        log.warning("unlink media file failed %s/%s: %s", topic_id, name, e)
    if s3_enabled():
        try:
            _s3_delete(topic_id, name)
        except Exception:  # noqa: BLE001
            log.warning("s3 delete failed topic=%s name=%s", topic_id, name, exc_info=True)


def public_url(media, base: str = "") -> str:
    """Row -> public URL (`{base}/api/ai-telemetry/public/topic-media/{tid}/{name}`).

    Empty base -> relative path, used directly by the frontend on the same origin / behind nginx reverse proxy; it is also what draft bodies embed.
    """
    name = Path(media.storage_path or "").name
    if not name:
        return ""
    return f"{base.rstrip('/')}{PUBLIC_PREFIX}/{media.topic_id}/{name}"


def parse_public_url(url: str) -> Optional[tuple[int, str]]:
    """Public URL -> (topic_id, filename); returns None if unrecognized (external link)."""
    m = _PUBLIC_URL_RE.search((url or "").split("?")[0].strip())
    return (int(m.group(1)), m.group(2)) if m else None


def local_file_from_url(url: str) -> Optional[Path]:
    """Public URL -> local file (returned only if available). Publish channels read from disk directly instead of calling our own HTTP endpoint."""
    parsed = parse_public_url(url)
    if not parsed:
        return None
    return fetch(*parsed)


def remove_topic_dir(topic_id: int) -> int:
    """Clean up assets together when deleting a topic (local directory + bucket prefix). Returns the number of files deleted."""
    d = media_root() / str(int(topic_id))
    n = 0
    if d.is_dir():
        n = sum(1 for p in d.rglob("*") if p.is_file())
        shutil.rmtree(d, ignore_errors=True)
    if s3_enabled():
        try:
            n = max(n, _s3_delete_prefix(topic_id))
        except Exception:  # noqa: BLE001
            log.warning("s3 delete prefix failed topic=%s", topic_id, exc_info=True)
    return n
