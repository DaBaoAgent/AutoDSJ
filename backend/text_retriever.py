from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from backend.embed_match import cosine, embed_texts

TEXT_EVENT_SCHEMA = "v1-subtitle-script-event-index"
TEXT_EMBED_CACHE = "_event_text_embeddings.json"
QUERY_EMBED_CACHE = "_query_text_embeddings.json"


def _norm(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def text_tokens(text: object) -> list[str]:
    """Tokenize Chinese drama text without a heavyweight segmenter.

    Character bigrams preserve names and short actions while ASCII words keep
    model numbers and foreign names searchable.  Full 2-6 character chunks are
    also retained so exact dialogue/name matches dominate fuzzy matches.
    """
    value = _norm(text).lower()
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value)
    result: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if 2 <= len(token) <= 6:
                result.append(token)
            result.extend(token[i:i + 2] for i in range(max(0, len(token) - 1)))
        else:
            result.append(token)
    return result


def _overlap(start: float, end: float, left: float, right: float) -> float:
    return max(0.0, min(end, right) - max(start, left))


def build_text_event_index(folder: Path, events: list[dict]) -> dict:
    """Combine scene map, source subtitles and reviewed script rows per event."""
    folder = folder.resolve()
    scene_map: dict = {}
    script: dict = {}
    for path, target in ((folder / "_scene_map.json", "scene"),
                         (folder / "_drama_script_table.json", "script")):
        if not path.exists():
            continue
        try:
            if target == "scene":
                scene_map = json.loads(path.read_text("utf-8"))
            else:
                script = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            pass
    scenes = {str(item.get("name")): item for item in scene_map.get("scenes", [])}
    source_rows = [row for row in script.get("rows", []) if row.get("row_type") == "source_clip"]
    documents = []
    for event in events:
        start, end = float(event["start"]), float(event["end"])
        scene = scenes.get(str(event.get("scene"))) or {}
        reviewed = []
        for row in source_rows:
            try:
                if _overlap(start, end, float(row["source_start"]), float(row["source_end"])) > 0:
                    reviewed.extend((str(row.get("text") or ""), str(row.get("visual_intent") or "")))
            except (KeyError, TypeError, ValueError):
                continue
        parts = [
            str(event.get("scene") or ""),
            " ".join(str(x) for x in scene.get("characters", [])),
            " ".join(str(x) for x in scene.get("keywords", [])),
            str(event.get("subtitle_text") or ""),
            " ".join(reviewed),
            " ".join(str(x) for x in event.get("people_evidence", [])),
        ]
        # Sparse visual evidence is useful when available, but is no longer a
        # prerequisite for building the event document.
        for frame in event.get("visual_evidence", []):
            parts.extend(str(frame.get(key) or "") for key in
                         ("caption", "people", "scene", "action", "props"))
        text = _norm(" ".join(parts))
        documents.append({
            "event_id": event.get("event_id"),
            "scene": event.get("scene"),
            "start": start,
            "end": end,
            "text": text,
            "subtitle_text": str(event.get("subtitle_text") or ""),
            "reviewed_script": _norm(" ".join(reviewed)),
            "scene_characters": list(scene.get("characters", [])),
            "scene_keywords": list(scene.get("keywords", [])),
        })
    payload = {"schema": TEXT_EVENT_SCHEMA, "event_count": len(documents), "events": documents}
    (folder / "_subtitle_event_index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return payload


class HybridTextRetriever:
    def __init__(self, documents: list[dict], dense_vectors: dict[str, list[float]] | None = None):
        self.documents = {str(item["event_id"]): item for item in documents}
        self.tokens = {key: text_tokens(item.get("text")) for key, item in self.documents.items()}
        self.lengths = {key: max(1, len(value)) for key, value in self.tokens.items()}
        self.avg_len = sum(self.lengths.values()) / max(1, len(self.lengths))
        self.dense_vectors = dense_vectors or {}
        df: Counter[str] = Counter()
        for tokens in self.tokens.values():
            df.update(set(tokens))
        n = max(1, len(self.tokens))
        self.idf = {term: math.log(1 + (n - count + 0.5) / (count + 0.5)) for term, count in df.items()}

    def bm25(self, query: str, event_id: str) -> float:
        query_terms = text_tokens(query)
        if not query_terms:
            return 0.0
        terms = Counter(self.tokens.get(str(event_id), []))
        dl = self.lengths.get(str(event_id), 1)
        score = 0.0
        for term in query_terms:
            tf = terms.get(term, 0)
            if not tf:
                continue
            score += self.idf.get(term, 0.0) * (tf * 2.2) / (
                tf + 1.2 * (0.25 + 0.75 * dl / max(1.0, self.avg_len)))
        # Compress unbounded BM25 into 0..1 for stable evidence weighting.
        return 1.0 - math.exp(-score / 4.0)

    def score(self, query: str, event_id: str, *, query_vector: list[float] | None = None,
              characters: list[str] | None = None, actions: list[str] | None = None) -> dict:
        doc = self.documents.get(str(event_id), {})
        text = str(doc.get("text") or "")
        lexical = self.bm25(query, str(event_id))
        exact_terms = [x for x in [*(characters or []), *(actions or [])] if len(str(x)) >= 2]
        exact = (sum(1 for term in exact_terms if str(term) in text) / len(exact_terms)) if exact_terms else 0.0
        quote = 0.0
        compact_query = re.sub(r"\W+", "", query)
        if len(compact_query) >= 4 and compact_query in re.sub(r"\W+", "", text):
            quote = 1.0
        dense = 0.0
        vector = self.dense_vectors.get(str(event_id))
        if query_vector and vector:
            dense = max(0.0, cosine(query_vector, vector))
        total = 0.43 * lexical + 0.34 * dense + 0.18 * exact + 0.05 * quote
        return {"total": total, "lexical": lexical, "dense": dense, "exact": exact, "quote": quote}


def dense_event_vectors(folder: Path, documents: list[dict], api_key: str) -> dict[str, list[float]]:
    if not api_key or not documents:
        return {}
    texts = [str(item.get("text") or "") for item in documents]
    signature = hashlib.sha256("\u0001".join(texts).encode("utf-8")).hexdigest()
    path = folder / TEXT_EMBED_CACHE
    if path.exists():
        try:
            cached = json.loads(path.read_text("utf-8"))
            if cached.get("signature") == signature and len(cached.get("vectors", [])) == len(texts):
                return {str(item["event_id"]): vector for item, vector in zip(documents, cached["vectors"])}
        except (OSError, ValueError, TypeError):
            pass
    vectors = embed_texts(texts, api_key, batch=10, workers=4)
    path.write_text(json.dumps({"signature": signature, "vectors": vectors}), "utf-8")
    return {str(item["event_id"]): vector for item, vector in zip(documents, vectors)}


def dense_query_vectors(folder: Path, texts: list[str], api_key: str) -> dict[str, list[float]]:
    """Embed unique narration queries once and reuse them on every rematch."""
    unique = list(dict.fromkeys(str(text) for text in texts if str(text).strip()))
    if not api_key or not unique:
        return {}
    signature = hashlib.sha256("\u0001".join(unique).encode("utf-8")).hexdigest()
    path = folder / QUERY_EMBED_CACHE
    if path.exists():
        try:
            cached = json.loads(path.read_text("utf-8"))
            if cached.get("signature") == signature and len(cached.get("vectors", [])) == len(unique):
                return {text: vector for text, vector in zip(unique, cached["vectors"])}
        except (OSError, ValueError, TypeError):
            pass
    vectors = embed_texts(unique, api_key, batch=10, workers=4)
    path.write_text(json.dumps({"signature": signature, "vectors": vectors}), "utf-8")
    return {text: vector for text, vector in zip(unique, vectors)}
