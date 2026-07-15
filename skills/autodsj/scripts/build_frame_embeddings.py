#!/usr/bin/env python3
"""Pre-build the DashScope text-embedding-v4 frame-vector cache for one episode.

Why: the解说→画面 semantic matcher (allocate_visual_all) embeds every visual
frame's caption once and caches the vectors to ``<素材夹>/_frame_embeddings.json``.
The FIRST render of a new episode pays that cost (~4 min for ~286 frames, 6-way
parallel); every later re-render reads the cache instantly. Running this ahead of
time means a subsequent ``dy run --skip-visual`` re-render is fast AND you can
confirm the cache built without kicking off a full render.

Run from ANYWHERE with the project venv (the project root is hard-wired below so
imports resolve regardless of cwd):

    D:/@kaifa/AutoDSJ/project/.venv/Scripts/python.exe \
        <this script> "D:/自动剪辑/玫瑰的故事/玫瑰的故事 第N集"

Requirements: the episode folder already has ``_source_visual_index.json``
(i.e. visual recognition finished), and DASHSCOPE_API_KEY is set (in
config/secrets.bin — the key that also powers 视觉识别/配音).

The cache signature is the SHA1 of the frame evidence texts, so we deliberately
reuse ``load_visual_frames(folder)`` -> ``f.evidence`` (the exact same source the
render pipeline uses); building the text any other way would produce a
non-matching signature and the render would rebuild from scratch.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Hard-wire the DY project root so `backend.*` imports resolve from any cwd.
PROJECT_ROOT = Path(r"D:\@kaifa\AutoDSJ\project")
sys.path.insert(0, str(PROJECT_ROOT))

from backend.embed_match import dashscope_key, frame_embeddings  # noqa: E402
from backend.visual_matcher import load_visual_frames  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('usage: build_frame_embeddings.py "<素材文件夹>"')
    folder = Path(sys.argv[1]).expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit(f"素材文件夹不存在：{folder}")
    key = dashscope_key()
    if not key:
        raise SystemExit("缺少 DASHSCOPE_API_KEY（config/secrets.bin）")

    frames = load_visual_frames(folder)          # SAME source the render uses
    frame_texts = [f.evidence for f in frames]   # SAME evidence -> signature matches
    print(f"构建帧向量缓存：{len(frame_texts)} 帧（首轮~4分钟，之后走缓存）…", flush=True)
    vectors = frame_embeddings(folder, frame_texts, key)
    print(f"完成：{len(vectors)} 向量 → {folder / '_frame_embeddings.json'}")


if __name__ == "__main__":
    main()
