"""本地人脸库（InsightFace / ArcFace）—— 视觉索引「认清是谁」的主力。

思路：为每部剧建 `_faces/<角色名>/*.jpg` 参考照，离线抽 512 维 ArcFace 人脸向量存
`_face_gallery.json`（一次建库全集复用）。识别时对每张抽帧检测人脸、抽向量、与库里
余弦比对，得到画面里「有谁·谁是主体·谁在左右」。纯本地、快、零 API 费，且比让 VL
盲猜身份准得多（明星正脸 >95%）。

产出两种渲染串：
- `render_known_people`  → 喂给 VL 的「已知人物」提示（角色名 + 位置/主次）。
- `render_people_field`  → 写进视觉索引 `people` 字段的「演员（饰角色）」串，
  直接驱动 `visual_matcher` 的角色建组 + 身份加分（±0.4/0.3），根治张冠李戴。

依赖：insightface + onnxruntime(CPU) + opencv + numpy。首次 `prepare` 会下载 buffalo_l
模型（~300MB）到 ~/.insightface。无 GPU 时走 CPUExecutionProvider。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

ROSTER_FILE = "roster.json"

# 年龄/时期变体后缀：同一角色的童年/成年脸 ArcFace 向量差异大，用户会分目录
# （如「方太初小时候」「方太初长大了」）→ 识别时分别比对更准，但对外身份归一到规范
# 角色名「方太初」，以匹配文案 + 满足 visual_matcher 角色名 2-5 字的建组正则。
_AGE_SUFFIXES = (
    "小时候", "长大了", "长大后", "长大", "小时", "童年", "幼年", "少年时", "少年",
    "青年时", "青年", "成年后", "成年", "中年", "老年", "年轻时", "年老", "年幼",
)


def _canonical_role(name: str) -> str:
    """把年龄/时期变体目录名归一到规范角色名（方太初小时候 → 方太初）。"""
    for suffix in _AGE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            return name[: -len(suffix)]
    return name


# 进程级单例，避免每帧重载模型
_APP = None
_APP_DET = 0


def _get_app(det_size: int = 640):
    """懒加载 InsightFace FaceAnalysis（buffalo_l, CPU）。首次触发模型下载。"""
    global _APP, _APP_DET
    if _APP is not None and _APP_DET == det_size:
        return _APP
    from insightface.app import FaceAnalysis  # 延迟导入，未装库时不影响其它命令

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(det_size, det_size))
    _APP, _APP_DET = app, det_size
    return app


def insightface_available() -> bool:
    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def _read_image(path: Path):
    import cv2
    import numpy as np

    # cv2.imread 在含中文路径的 Windows 上会失败 → 用 imdecode 读字节
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _largest_face(faces):
    if not faces:
        return None
    return max(faces, key=lambda f: float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])))


def _load_roster(faces_root: Path) -> dict[str, str]:
    path = faces_root / ROSTER_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def build_gallery(faces_root: Path, *, det_size: int = 640) -> dict:
    """扫描 `_faces/<角色名>/*.jpg` 建人脸库，返回并写入 `_face_gallery.json`。

    每个角色目录下所有清晰正脸各抽一条 512 维向量（已 L2 归一化）。
    """
    faces_root = Path(faces_root)
    if not faces_root.is_dir():
        raise RuntimeError(f"人脸参考目录不存在：{faces_root}")
    app = _get_app(det_size)
    roster = _load_roster(faces_root)
    roles: dict[str, list[list[float]]] = {}
    canonical: dict[str, str] = {}
    skipped: list[str] = []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    for role_dir in sorted(p for p in faces_root.iterdir() if p.is_dir()):
        role = role_dir.name
        vectors: list[list[float]] = []
        for img_path in sorted(role_dir.iterdir()):
            if img_path.suffix.lower() not in exts:
                continue
            image = _read_image(img_path)
            if image is None:
                skipped.append(str(img_path))
                continue
            face = _largest_face(app.get(image))
            if face is None or getattr(face, "normed_embedding", None) is None:
                skipped.append(str(img_path))
                continue
            vectors.append([float(x) for x in face.normed_embedding])
        if vectors:
            roles[role] = vectors
            canonical[role] = _canonical_role(role)
    gallery = {
        "roster": roster,
        "roles": roles,
        "canonical": canonical,
        "det_size": det_size,
        "role_count": len(roles),
        "canonical_count": len(set(canonical.values())),
        "vector_count": sum(len(v) for v in roles.values()),
        "skipped": skipped,
        "created_at": time.time(),
    }
    return gallery


def save_gallery(gallery: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(gallery, ensure_ascii=False), "utf-8")


def load_gallery(path: Path) -> Optional[dict]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
        if data.get("roles"):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


class FaceIdentifier:
    """加载好的人脸库 + 识别器。对每张抽帧返回结构化身份。"""

    def __init__(self, gallery: dict, *, threshold: float = 0.38,
                 min_size: int = 46, det_size: int = 640):
        import numpy as np

        self.roster: dict[str, str] = gallery.get("roster", {})
        self.canonical: dict[str, str] = gallery.get("canonical", {})
        self.threshold = float(threshold)
        self.min_size = int(min_size)
        self.det_size = int(gallery.get("det_size") or det_size)
        # 预堆叠每个角色的向量矩阵，识别时批量点积（向量已 L2 归一化 → 点积=余弦）
        self._roles: list[str] = []
        self._mats: list = []
        for role, vecs in gallery.get("roles", {}).items():
            if not vecs:
                continue
            self._roles.append(role)
            self._mats.append(np.asarray(vecs, dtype="float32"))
        self._app = None

    def _ensure_app(self):
        if self._app is None:
            self._app = _get_app(self.det_size)
        return self._app

    def identify(self, image_path: str, frame_w: int = 0, frame_h: int = 0) -> list[dict]:
        import numpy as np

        image = _read_image(Path(image_path))
        if image is None or not self._roles:
            return []
        if not frame_w or not frame_h:
            frame_h, frame_w = image.shape[:2]
        faces = self._ensure_app().get(image)
        results: list[dict] = []
        frame_area = max(1.0, float(frame_w) * float(frame_h))
        for face in faces:
            emb = getattr(face, "normed_embedding", None)
            if emb is None:
                continue
            x1, y1, x2, y2 = [float(v) for v in face.bbox]
            w, h = x2 - x1, y2 - y1
            if min(w, h) < self.min_size:
                continue
            q = np.asarray(emb, dtype="float32")
            best_role, best_score = "", -1.0
            for role, mat in zip(self._roles, self._mats):
                score = float(np.max(mat @ q))  # 该角色所有参考照中的最高相似度
                if score > best_score:
                    best_role, best_score = role, score
            if best_score < self.threshold:
                continue
            canon = self.canonical.get(best_role, best_role)  # 年龄变体归一到规范角色名
            cx = (x1 + x2) / 2.0
            position = "左" if cx < frame_w * 0.38 else ("右" if cx > frame_w * 0.62 else "居中")
            results.append({
                "role": canon,
                "actor": self.roster.get(canon, ""),
                "score": round(best_score, 3),
                "box": [round(x1), round(y1), round(w), round(h)],
                "area_ratio": round((w * h) / frame_area, 4),
                "position": position,
                "det_score": round(float(getattr(face, "det_score", 0.0)), 3),
            })
        # 按人脸面积降序 → 最大者为画面主体
        results.sort(key=lambda r: r["area_ratio"], reverse=True)
        for rank, r in enumerate(results):
            r["prominence"] = "主体" if rank == 0 else "次要"
        return results


def render_known_people(identified: list[dict]) -> str:
    """喂给 VL 的「已知人物」提示：角色名 + 位置/主次（不含演员名，避免干扰描述）。"""
    parts = []
    for r in identified:
        tags = [t for t in (r.get("position"), r.get("prominence")) if t]
        parts.append(f"{r['role']}（{'·'.join(tags)}）" if tags else r["role"])
    return "、".join(parts)


def render_people_field(identified: list[dict]) -> str:
    """写进视觉索引 people 字段：优先「演员（饰角色）」以驱动 visual_matcher 角色建组。

    无演员名时退回角色名（若该角色本名在 _NICKNAME_BRIDGE 里仍可成组）。
    """
    seen: set[str] = set()
    parts = []
    for r in identified:
        role = r.get("role", "")
        if not role or role in seen:
            continue
        seen.add(role)
        actor = r.get("actor", "")
        parts.append(f"{actor}（饰{role}）" if actor else role)
    return "；".join(parts)
