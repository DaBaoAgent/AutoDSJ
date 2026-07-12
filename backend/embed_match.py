"""Embedding-based semantic matching for narration → visual frame alignment.

Uses DashScope text-embedding-v4. Frame embeddings are cached to
`_frame_embeddings.json` inside the episode folder so they are computed once
(slow) and reused on every re-render (instant).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

from backend.config_store import read_env, read_secrets

_EMBED_URL = ("https://dashscope.aliyuncs.com/api/v1/services/"
              "embeddings/text-embedding/text-embedding")
_CACHE_FILE = "_frame_embeddings.json"


def dashscope_key() -> str:
    return read_secrets().get("dashscope_api_key") or read_env().get("DASHSCOPE_API_KEY", "")


def _force_ipv4() -> None:
    """Pin DNS to IPv4 — Windows can otherwise pick an unreachable IPv6 route and
    stall the TLS handshake (same reason the render pipeline does this)."""
    import socket
    original = socket.getaddrinfo

    def getaddrinfo_v4(host, port, family=0, type=0, proto=0, flags=0):
        return original(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = getaddrinfo_v4


def _embed_one_batch(chunk: list[str], api_key: str) -> list[list[float]]:
    chunk = [(t[:1800] or "空") for t in chunk]
    body = json.dumps({"model": "text-embedding-v4",
                       "input": {"texts": chunk},
                       "parameters": {"text_type": "query"}}).encode("utf-8")
    req = urllib.request.Request(
        _EMBED_URL, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    payload = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if attempt == 3:
                raise RuntimeError(f"Embedding API {exc.code}: {detail}") from exc
            time.sleep(2 * (attempt + 1))
        except Exception as exc:
            if attempt == 3:
                raise RuntimeError(f"Embedding API failed: {exc}") from exc
            time.sleep(2 * (attempt + 1))
    embeddings = (payload or {}).get("output", {}).get("embeddings", [])
    embeddings.sort(key=lambda item: item.get("text_index", 0))
    return [[float(v) for v in (item.get("embedding") or item.get("vector") or [])]
            for item in embeddings]


def embed_texts(texts: list[str], api_key: str, batch: int = 10, workers: int = 6) -> list[list[float]]:
    """Embed a list of texts. Batches (10/req) are dispatched concurrently to hide
    per-request network latency; results are stitched back in input order."""
    _force_ipv4()
    if not texts:
        return []
    offsets = list(range(0, len(texts), batch))
    results: dict[int, list[list[float]]] = {}
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(workers, len(offsets))) as pool:
        futures = {pool.submit(_embed_one_batch, texts[o:o + batch], api_key): o for o in offsets}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    out: list[list[float]] = []
    for o in offsets:
        out.extend(results[o])
    return out


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / max(na * nb, 1e-12)


def _frame_signature(frame_texts: list[str]) -> str:
    joined = "\u0001".join(frame_texts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def frame_embeddings(folder: Path, frame_texts: list[str], api_key: str) -> list[list[float]]:
    """Return one embedding per frame, cached to disk keyed by a content signature.

    The signature invalidates the cache automatically if the visual index changes.
    """
    cache_path = folder / _CACHE_FILE
    signature = _frame_signature(frame_texts)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text("utf-8"))
            if cached.get("signature") == signature and len(cached.get("vectors", [])) == len(frame_texts):
                return [list(map(float, vec)) for vec in cached["vectors"]]
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
    vectors = embed_texts(frame_texts, api_key)
    try:
        cache_path.write_text(json.dumps({"signature": signature, "vectors": vectors}), "utf-8")
    except OSError:
        pass
    return vectors
