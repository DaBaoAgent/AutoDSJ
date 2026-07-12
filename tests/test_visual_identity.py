"""视觉索引身份注入链路测试（2026-07 精度重构）。

覆盖不依赖 insightface 模型的纯逻辑：人脸结果渲染、身份覆盖到帧、
以及注入的 people 字段能驱动 visual_matcher 角色建组 + 命中判定。
"""

from backend.face_gallery import render_known_people, render_people_field
from backend.vision_api import _render_people
from backend.drama_source_index import _apply_identity
import backend.visual_matcher as vm


def test_render_people_structured_list():
    text, detail = _render_people([
        {"name": "黄亦玫", "speaking": True, "position": "居中", "doing": "说话"},
        {"name": "庄国栋", "position": "右", "doing": "倾听"},
    ])
    assert "黄亦玫" in text and "庄国栋" in text
    assert len(detail) == 2
    # speaking=True 且 doing 已含"说" → 不重复加"说话"
    assert text.count("说话") == 1


def test_render_people_plain_string_passthrough():
    text, detail = _render_people("穿红裙的女子")
    assert text == "穿红裙的女子"
    assert detail == []


def test_render_people_field_actor_role():
    ident = [
        {"role": "黄亦玫", "actor": "刘亦菲"},
        {"role": "庄国栋", "actor": "彭冠英"},
    ]
    field = render_people_field(ident)
    assert "刘亦菲（饰黄亦玫）" in field
    assert "彭冠英（饰庄国栋）" in field


def test_render_people_field_role_only_when_no_actor():
    assert render_people_field([{"role": "路人甲", "actor": ""}]) == "路人甲"


def test_render_known_people_has_position():
    ident = [{"role": "黄亦玫", "position": "居中", "prominence": "主体"}]
    assert render_known_people(ident) == "黄亦玫（居中·主体）"


def test_apply_identity_overwrites_people_and_prefixes_caption():
    frames = [{"frame_id": "a", "caption": "两人在客厅交谈"}]
    idmap = {"a": [
        {"role": "黄亦玫", "actor": "刘亦菲"},
        {"role": "庄国栋", "actor": "彭冠英"},
    ]}
    _apply_identity(frames, idmap)
    assert "刘亦菲（饰黄亦玫）" in frames[0]["people"]
    assert frames[0]["caption"].startswith("【黄亦玫、庄国栋】")
    assert frames[0]["identified"] == idmap["a"]


def test_apply_identity_no_map_is_noop():
    frames = [{"frame_id": "a", "caption": "x", "people": "原描述"}]
    _apply_identity(frames, None)
    _apply_identity(frames, {})
    assert frames[0]["people"] == "原描述"


def test_injected_identity_drives_matcher_grouping():
    """注入的「演员（饰角色）」应让 visual_matcher 建组，且解说点名能命中/避让。"""
    frames = [
        {"frame_id": "a", "caption": "两人交谈"},
        {"frame_id": "b", "caption": "一个女人站着"},
    ]
    idmap = {
        "a": [{"role": "黄亦玫", "actor": "刘亦菲"}, {"role": "庄国栋", "actor": "彭冠英"}],
        "b": [{"role": "黄亦玫", "actor": "刘亦菲"}],
    }
    _apply_identity(frames, idmap)
    vm.register_character_aliases(frames)
    # 应建出黄亦玫组与庄国栋组
    joined = [set(g) for g in vm._CHAR_GROUPS]
    assert any("黄亦玫" in g for g in joined)
    assert any("庄国栋" in g for g in joined)
    # 解说点名"庄国栋" → frame a(有庄国栋) 命中，frame b(只有玫瑰) 不命中庄国栋组
    narr_hits = vm._character_hits("庄国栋礼貌克制")
    a_hits = vm._character_hits(vm._frame_text(frames[0]))
    b_hits = vm._character_hits(vm._frame_text(frames[1]))
    assert narr_hits & a_hits      # frame a 与解说同命中庄国栋组 → 加分
    assert not (narr_hits & b_hits)  # frame b 不含庄国栋组 → 不会被误选


def test_nickname_bridge_still_fires_on_injected_identity():
    frames = [{"frame_id": "a", "caption": "x"}]
    _apply_identity(frames, {"a": [{"role": "黄亦玫", "actor": "刘亦菲"}]})
    vm.register_character_aliases(frames)
    # 文案用昵称"玫瑰"应桥接到黄亦玫组
    assert vm._character_hits("玫瑰独自伤心") & vm._character_hits(vm._frame_text(frames[0]))
