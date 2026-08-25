"""Validate and apply Codex-reviewed parent-folder decisions.

Reads ``review_decisions.json`` and ``ambiguous.jsonl`` from the state
directory. The preflight rejects missing/duplicate decisions and any placement
folder that was not an exact candidate for that chart before writing files.
"""

import argparse
import csv
import io
import json
import os
import sys
import zipfile


CHART_EXT = (".bms", ".bme", ".bml", ".pms", ".bmson")
BGA_EXT = (".bmp", ".png", ".jpg", ".jpeg", ".gif", ".wmv", ".mp4", ".avi", ".mov", ".webm", ".mpg", ".mpeg")
AUDIO_EXT = (".wav", ".ogg", ".mp3", ".flac", ".m4a", ".opus")
ALLOWED_EXT = CHART_EXT + BGA_EXT + AUDIO_EXT


def _load_jsonl(path):
    with open(path, "r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _preflight(decisions, cases):
    case_by_md5 = {}
    decision_by_md5 = {}
    errors = []

    for index, case in enumerate(cases):
        md5 = case.get("md5") if isinstance(case, dict) else None
        if not isinstance(md5, str) or not md5:
            errors.append(f"ambiguous case {index}: missing md5")
        elif md5 in case_by_md5:
            errors.append(f"{md5}: duplicate ambiguous case")
        else:
            case_by_md5[md5] = case

    if not isinstance(decisions, list):
        return {}, ["review_decisions.json must contain a JSON array"]

    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f"decision {index}: expected an object")
            continue
        md5 = decision.get("md5")
        if not isinstance(md5, str) or not md5:
            errors.append(f"decision {index}: missing md5")
            continue
        if md5 in decision_by_md5:
            errors.append(f"{md5}: duplicate decision")
            continue
        decision_by_md5[md5] = decision
        case = case_by_md5.get(md5)
        if case is None:
            errors.append(f"{md5}: not present in ambiguous.jsonl")
            continue

        action = decision.get("decision")
        if action not in {"place", "skip"}:
            errors.append(f"{md5}: decision must be place or skip")
            continue
        if action == "place":
            folder = decision.get("folder")
            candidates = {
                candidate.get("folder") for candidate in case.get("candidates", [])
            }
            if not isinstance(folder, str) or folder not in candidates:
                errors.append(f"{md5}: placement folder is not an exact candidate")

    missing = sorted(set(case_by_md5) - set(decision_by_md5))
    if missing:
        preview = ", ".join(missing[:5])
        suffix = " ..." if len(missing) > 5 else ""
        errors.append(f"missing {len(missing)} decisions: {preview}{suffix}")
    return decision_by_md5, errors


def _write_zip_members(blob, target):
    placed = []
    skipped_existing = []
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for member in archive.namelist():
            if member.endswith("/"):
                continue
            basename = os.path.basename(member.replace("\\", "/"))
            if not basename or not basename.lower().endswith(ALLOWED_EXT):
                continue
            destination = os.path.join(target, basename)
            if os.path.exists(destination):
                skipped_existing.append(basename)
                continue
            with open(destination, "wb") as stream:
                stream.write(archive.read(member))
            placed.append(basename)
    if not placed and not skipped_existing:
        raise ValueError("archive contains no supported chart-side files")
    return placed, skipped_existing


def _write_raw_chart(blob, target, md5):
    head = blob[:200].lower()
    if not any(marker in head for marker in (b"#title", b"#player", b"#genre")):
        raise ValueError("not zip and not bms")
    basename = f"{md5[:12]}.bms"
    destination = os.path.join(target, basename)
    if os.path.exists(destination):
        return [], [basename]
    with open(destination, "wb") as stream:
        stream.write(blob)
    return [basename], []


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--music-root", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    decisions_path = os.path.join(args.state_dir, "review_decisions.json")
    ambiguous_path = os.path.join(args.state_dir, "ambiguous.jsonl")
    with open(decisions_path, "r", encoding="utf-8") as stream:
        decisions = json.load(stream)
    cases = _load_jsonl(ambiguous_path)
    decision_by_md5, validation_errors = _preflight(decisions, cases)
    if validation_errors:
        print("validation failed; no files were written", file=sys.stderr)
        for error in validation_errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    folders = {
        name: os.path.join(args.music_root, name)
        for name in os.listdir(args.music_root)
        if os.path.isdir(os.path.join(args.music_root, name))
    }
    download_cache = os.path.join(args.state_dir, "downloads")
    log_rows = []
    placed_count = skipped_count = error_count = 0

    for case in cases:
        md5 = case["md5"]
        decision = decision_by_md5[md5]
        folder_name = decision.get("folder")
        reason = decision.get("reason", "")
        if decision["decision"] == "skip":
            skipped_count += 1
            log_rows.append([md5, "skip", "", reason, "", ""])
            continue
        if folder_name not in folders:
            error_count += 1
            log_rows.append([md5, "error", folder_name, "folder not found on disk", "", ""])
            continue

        cache_path = os.path.join(download_cache, md5 + ".bin")
        if not os.path.isfile(cache_path):
            error_count += 1
            log_rows.append([md5, "error", folder_name, "cache file missing", "", ""])
            continue
        with open(cache_path, "rb") as stream:
            blob = stream.read()
        try:
            if blob[:2] == b"PK":
                placed, skipped_existing = _write_zip_members(blob, folders[folder_name])
            else:
                placed, skipped_existing = _write_raw_chart(blob, folders[folder_name], md5)
            placed_count += 1
            log_rows.append(
                [
                    md5,
                    "placed",
                    folder_name,
                    reason,
                    ";".join(placed),
                    ";".join(skipped_existing),
                ]
            )
        except Exception as exc:
            error_count += 1
            log_rows.append([md5, "error", folder_name, str(exc), "", ""])

    log_path = os.path.join(args.state_dir, "review_apply_log.csv")
    with open(log_path, "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["md5", "status", "folder", "reason", "placed", "skipped_existing"])
        writer.writerows(log_rows)

    print(f"placed: {placed_count}")
    print(f"skipped: {skipped_count}")
    print(f"errors: {error_count}")
    print(f"log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
