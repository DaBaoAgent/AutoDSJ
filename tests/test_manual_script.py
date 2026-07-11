from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from backend.manual_script import (
    ScriptBlock,
    SubtitleLine,
    _clean_for_match,
    match_source_block,
    parse_srt_file,
)


def subtitle(index: int, start: float, end: float, text: str) -> SubtitleLine:
    return SubtitleLine(index, start, end, text, _clean_for_match(text))


class MatchSourceBlockTests(unittest.TestCase):
    def test_parses_ass_dialogue_lines_for_manual_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "episode.ass"
            path.write_text(
                "[Script Info]\nTitle: test\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:01:02.20,0:01:04.50,Default,,0,0,0,,{\\an8}第一句\\N第二句\n",
                "utf-8",
            )

            lines = parse_srt_file(path)

            self.assertEqual(len(lines), 1)
            self.assertAlmostEqual(lines[0].start, 62.2)
            self.assertAlmostEqual(lines[0].end, 64.5)
            self.assertEqual(lines[0].text, "第一句 第二句")

    def test_keeps_separated_requested_lines_as_separate_intervals(self) -> None:
        subtitles = [
            subtitle(1, 10.0, 11.0, "第一句"),
            subtitle(2, 11.2, 14.0, "没有指定的长对白"),
            subtitle(3, 20.0, 21.0, "第二句"),
            subtitle(4, 21.2, 25.0, "另一段没有指定的对白"),
            subtitle(5, 30.0, 31.0, "第三句"),
        ]
        block = ScriptBlock("source_clip", "第一句\n第二句\n第三句", "")

        result = match_source_block(block, subtitles)

        self.assertEqual(result["match_mode"], "exact_script_lines")
        self.assertEqual(
            [(part["start"], part["end"]) for part in result["intervals"]],
            [(10.0, 11.0), (20.0, 21.0), (30.0, 31.0)],
        )

    def test_merges_only_adjacent_requested_subtitles(self) -> None:
        subtitles = [
            subtitle(1, 10.0, 11.0, "第一句"),
            subtitle(2, 11.3, 12.0, "第二句"),
            subtitle(3, 12.1, 13.0, "未指定对白"),
            subtitle(4, 14.0, 15.0, "第三句"),
        ]
        block = ScriptBlock("source_clip", "第一句\n第二句\n第三句", "")

        result = match_source_block(block, subtitles)

        self.assertEqual(
            [(part["start"], part["end"]) for part in result["intervals"]],
            [(10.0, 12.0), (14.0, 15.0)],
        )


if __name__ == "__main__":
    unittest.main()
