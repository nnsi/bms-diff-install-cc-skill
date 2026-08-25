"""Prepare compact ambiguous-placement cases for Codex model review."""

import argparse
import json
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    source = os.path.join(args.state_dir, "ambiguous.jsonl")
    destination = os.path.join(args.state_dir, "review_input.json")
    with open(source, "r", encoding="utf-8") as stream:
        cases = [json.loads(line) for line in stream if line.strip()]

    slim = []
    for case in cases:
        slim.append(
            {
                "md5": case["md5"],
                "table_title": case.get("title"),
                "table_artist": case.get("artist"),
                "bms_title": case.get("bms_title"),
                "bms_artist": case.get("bms_artist"),
                "total_wavs": case.get("total_wavs"),
                "candidates": [
                    {"folder": candidate["folder"], "hits": candidate["hits"]}
                    for candidate in case.get("candidates", [])[:10]
                ],
            }
        )

    with open(destination, "w", encoding="utf-8") as stream:
        json.dump(slim, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"wrote {len(slim)} cases to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
