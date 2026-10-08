"""Alibaba Cloud content security external check client (P2, design doc 5.7) -- text TextModeration + image ImageModeration.

Positioning: a second layer of **soft signals** beyond the internal lexicon -- does not change internal gate verdicts and blocks no pipeline.
Only called additionally at the submit-for-review hook (hook 2); exit hooks 3/4 never call out.

Calls the OpenAPI directly with native ACS3-HMAC-SHA256 signing, without pulling in the whole alibabacloud_* SDK family
(keeps the main backend dependency surface constant; the signing algorithm is public and stable).

Credentials only via env: ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET;
missing env = feature automatically disabled (is_enabled() False, transparent to callers).

riskLevel handling metric definition (official recommendation): high/medium yield an "external check suggestion"; low is ignored (same as not detected).

Image side (integrated 2026-08-18): sends a **public image URL** for Alibaba Cloud to fetch from the source; no base64, no OSS upload.
The response is simpler than the text side -- Data.RiskLevel and Data.Result[].Label directly, no chunking or aggregation needed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

_ENDPOINT = os.environ.get("GEO_ALIYUN_GREEN_ENDPOINT", "green-cip.cn-shanghai.aliyuncs.com")
_ACTION = "TextModeration"        # Text-side default; image side passes ImageModeration
_VERSION = "2022-03-02"
_TIMEOUT_S = float(os.environ.get("GEO_EXTERNAL_SCAN_TIMEOUT_S", "3"))
_CHUNK_CHARS = 500          # API limit is 600; leave headroom
_MAX_CHUNKS = 6             # Max chunks sent per draft (controls cost + review latency)
_TOTAL_BUDGET_S = 8.0       # Total external check latency budget per draft; when exceeded, stop early with a partial marker

_RISK_RANK = {"low": 1, "medium": 2, "high": 3}


def is_enabled() -> bool:
    return bool(os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
                and os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET"))


# -- ACS3-HMAC-SHA256 signing (Alibaba Cloud OpenAPI V3) -----------------------------

def _pct(s: str) -> str:
    return quote(s, safe="-_.~")


def _signed_request(params: dict[str, str], action: str = _ACTION) -> requests.Response:
    ak = os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"]
    sk = os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"]

    query = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    body_hash = hashlib.sha256(b"").hexdigest()      # RPC params all go in the query; body is empty
    headers = {
        "host": _ENDPOINT,
        "x-acs-action": action,
        "x-acs-version": _VERSION,
        "x-acs-date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "x-acs-signature-nonce": uuid.uuid4().hex,
        "x-acs-content-sha256": body_hash,
    }
    signed_names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical = f"POST\n/\n{query}\n{canonical_headers}\n{signed_names}\n{body_hash}"
    to_sign = "ACS3-HMAC-SHA256\n" + hashlib.sha256(canonical.encode()).hexdigest()
    signature = hmac.new(sk.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"ACS3-HMAC-SHA256 Credential={ak},SignedHeaders={signed_names},Signature={signature}"
    )
    return requests.post(
        f"https://{_ENDPOINT}/?{query}", headers=headers, timeout=_TIMEOUT_S,
    )


def _moderate_chunk(content: str, service: str) -> dict:
    """Check one text chunk. Returns {code, labels, risk_level, risk_words, risk_tips, request_id}."""
    params = {
        "Service": service,
        "ServiceParameters": json.dumps({"content": content}, ensure_ascii=False),
    }
    last_exc: Exception | None = None
    for attempt in (0, 1):
        try:
            resp = _signed_request(params)
            data = resp.json()
            code = int(data.get("Code") or resp.status_code)
            if code in (500, 581) and attempt == 0:
                time.sleep(0.5)
                continue
            d = data.get("Data") or {}
            # Native HTTPS returns lowercase keys (labels/reason); the official doc tables use the SDK's uppercase keys -- both are supported
            reason = {}
            try:
                reason = json.loads(d.get("Reason") or d.get("reason") or "{}")
            except Exception:  # noqa: BLE001
                pass
            return {
                "code": code,
                "labels": [x for x in (d.get("Labels") or d.get("labels") or "").split(",") if x],
                "risk_level": str(reason.get("riskLevel") or ""),
                "risk_words": [w for w in str(reason.get("riskWords") or "").split(",") if w],
                "risk_tips": str(reason.get("riskTips") or ""),
                "request_id": str(data.get("RequestId") or ""),
            }
        except Exception as e:  # noqa: BLE001 -- network / signing / parsing exceptions uniformly degrade
            last_exc = e
            if attempt == 0:
                time.sleep(0.5)
    raise last_exc if last_exc else RuntimeError("external moderation failed")


def _split_chunks(text: str) -> tuple[list[str], bool]:
    """Chunk by aggregating paragraphs, each chunk <= _CHUNK_CHARS, at most _MAX_CHUNKS chunks. Returns (chunks, truncated)."""
    out: list[str] = []
    buf = ""
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > _CHUNK_CHARS:      # Hard-split overlong single paragraphs
            out.append(para[:_CHUNK_CHARS])
            para = para[_CHUNK_CHARS:]
        if len(buf) + len(para) + 1 > _CHUNK_CHARS:
            if buf:
                out.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        out.append(buf)
    return out[:_MAX_CHUNKS], len(out) > _MAX_CHUNKS


def moderate_text(text: str, service: str = "ad_compliance_detection") -> dict:
    """External check of a whole draft. Never raises; returns status=degraded on failure.

    Returns {service, status: ok/partial/degraded/skipped, risk_level, labels,
          risk_words:[{word, tips, level}], request_ids, elapsed_ms, truncated}
    """
    if not is_enabled():
        return {"service": service, "status": "skipped", "reason": "no_credentials"}

    started = time.monotonic()
    chunks, truncated = _split_chunks(text)
    labels: set[str] = set()
    words: dict[str, dict] = {}
    request_ids: list[str] = []
    worst = ""
    status = "ok"
    try:
        for chunk in chunks:
            if time.monotonic() - started > _TOTAL_BUDGET_S:
                status = "partial"
                break
            r = _moderate_chunk(chunk, service)
            if r["code"] != 200:
                # 408 permission / 588 rate limit / 400 params -- do not retry the whole draft, degrade and stop
                log.warning("external moderation code=%s", r["code"])
                status = "degraded"
                break
            request_ids.append(r["request_id"])
            labels.update(r["labels"])
            lvl = r["risk_level"]
            if _RISK_RANK.get(lvl, 0) > _RISK_RANK.get(worst, 0):
                worst = lvl
            for w in r["risk_words"]:
                if w not in words:
                    words[w] = {"word": w, "tips": r["risk_tips"], "level": lvl}
    except Exception:  # noqa: BLE001
        log.exception("external moderation degraded")
        status = "degraded"

    return {
        "service": service,
        "status": status,
        "risk_level": worst,
        "labels": sorted(labels),
        "risk_words": list(words.values()),
        "request_ids": request_ids,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "truncated": bool(truncated),
    }


# -- Image external check (2026-08-18, image side of items 9/10 in the filing correction list) --------------------

_IMAGE_ACTION = "ImageModeration"
_IMAGE_SERVICE = os.environ.get("GEO_EXTERNAL_IMAGE_SERVICE", "baselineCheck")
_IMAGE_TIMEOUT_S = float(os.environ.get("GEO_EXTERNAL_IMAGE_TIMEOUT_S", "10"))


def moderate_image(image_url: str, service: str = "") -> dict:
    """External check of a single image. Never raises; returns status=degraded on failure.

    image_url must be a public address **Alibaba Cloud can fetch** (ours goes via /api/ai-telemetry/public/topic-media/...),
    so this path does not work in local dev -- verify on the test host / prod.

    Returns {service, status: ok/degraded/skipped, risk_level, labels, descriptions,
          request_ids, elapsed_ms}; fields align with moderate_text so the frontend uses one rendering logic for both.
    """
    service = service or _IMAGE_SERVICE
    if not is_enabled():
        return {"service": service, "status": "skipped", "reason": "no_credentials"}
    if not (image_url or "").startswith(("http://", "https://")):
        return {"service": service, "status": "skipped", "reason": "bad_url"}

    started = time.monotonic()
    params = {
        "Service": service,
        "ServiceParameters": json.dumps({"imageUrl": image_url}, ensure_ascii=False),
    }
    for attempt in (0, 1):
        try:
            resp = _signed_request(params, action=_IMAGE_ACTION)
            data = resp.json() if resp.content else {}
            code = int(data.get("Code") or resp.status_code)
            if code in (500, 581) and attempt == 0:      # System error / rate limit, back off once
                time.sleep(0.5)
                continue
            if code != 200:
                log.warning("image moderation code=%s msg=%s", code, str(data.get("Msg"))[:120])
                return {"service": service, "status": "degraded", "code": code,
                        "reason": str(data.get("Msg") or "")[:120],
                        "elapsed_ms": int((time.monotonic() - started) * 1000)}
            d = data.get("Data") or {}
            results = [r for r in (d.get("Result") or []) if isinstance(r, dict)]
            # nonLabel = no risk detected; not surfaced to the frontend as a label
            labels = sorted({str(r.get("Label") or "") for r in results} - {"", "nonLabel"})
            descriptions = sorted({str(r.get("Description") or "") for r in results if r.get("Description")})
            # Overall level from Data.RiskLevel; if missing, take the highest from the individual Result entries
            worst = str(d.get("RiskLevel") or "")
            if not worst:
                for r in results:
                    lv = str(r.get("RiskLevel") or "")
                    if _RISK_RANK.get(lv, 0) > _RISK_RANK.get(worst, 0):
                        worst = lv
            return {
                "service": service, "status": "ok",
                "risk_level": "" if worst == "none" else worst,
                "labels": labels,
                "descriptions": descriptions,
                "request_ids": [str(data.get("RequestId") or "")],
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        except Exception as e:  # noqa: BLE001 -- network / signing / parsing exceptions uniformly degrade
            if attempt == 0:
                time.sleep(0.5)
                continue
            log.warning("image moderation degraded: %s", e)
            return {"service": service, "status": "degraded", "reason": str(e)[:120],
                    "elapsed_ms": int((time.monotonic() - started) * 1000)}
    return {"service": service, "status": "degraded", "reason": "retry_exhausted",
            "elapsed_ms": int((time.monotonic() - started) * 1000)}


def moderate_media_row(media, *, base_url: str = "") -> dict:
    """External check for one TopicMediaORM: build the public URL -> submit. Videos are skipped (images only in this phase)."""
    if (getattr(media, "kind", "image") or "image") != "image":
        return {"status": "skipped", "reason": "not_image"}
    path = getattr(media, "storage_path", "") or ""
    if not path:
        return {"status": "skipped", "reason": "no_file"}
    from geo.services import media_storage
    base = (base_url or os.environ.get("GEO_PUBLIC_BASE_URL", "https://vigilath.cn")).rstrip("/")
    # The route prefix is /ai-telemetry (main.py mounts /api + the router has its own /ai-telemetry);
    # missing this segment makes Alibaba Cloud's fetch get a 404, degrading the whole external check
    return moderate_image(media_storage.public_url(media, base))
