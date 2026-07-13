from __future__ import annotations

import argparse
import json
from pathlib import Path


def overlap(start: float, end: float, ranges: list[list[float]]) -> float:
    length = max(0.001, end - start)
    return max((max(0.0, min(end, right) - max(start, left)) / length
                for left, right in ranges), default=0.0)


def evaluate(report: dict, gold: dict) -> dict:
    narration = [item for item in report.get("segments", [])
                 if item.get("row_type") == "narration"]
    results = []
    for case in gold.get("cases", []):
        matches = [item for item in narration if case["contains"] in str(item.get("text") or "")]
        best = None
        for item in matches:
            score = overlap(float(item.get("clip_start", 0)), float(item.get("clip_end", 0)),
                            case.get("accepted_ranges", []))
            if best is None or score > best[0]:
                best = (score, item)
        item = best[1] if best else {}
        results.append({"id": case["id"], "query": case["contains"],
                        "found": bool(matches), "overlap": round(best[0], 4) if best else 0.0,
                        "passed": bool(best and best[0] >= 0.5),
                        "clip": [item.get("clip_start"), item.get("clip_end")]})
    passed = sum(bool(item["passed"]) for item in results)
    return {"passed": passed, "total": len(results),
            "accuracy": round(passed / max(1, len(results)), 4), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--report", default="\u2605 \u5339\u914d\u62a5\u544a.json")
    parser.add_argument("--gold", type=Path,
                        default=Path(__file__).parents[1] / "benchmarks" / "episode5_action_gold.json")
    args = parser.parse_args()
    report = json.loads((args.folder / args.report).read_text("utf-8"))
    gold = json.loads(args.gold.read_text("utf-8"))
    result = evaluate(report, gold)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
