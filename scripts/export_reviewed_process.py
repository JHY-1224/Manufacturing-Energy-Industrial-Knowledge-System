"""Export visually reviewed process references, with independent per-tag quotas.

Raw candidates and rejected images are preserved. Only explicit reviewer decisions
are eligible; caption screening and the candidate ceiling never satisfy quotas.
"""
import argparse
import collections
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def export(root, destination):
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if not destination.is_relative_to(root) or destination == root:
        raise ValueError("Reviewed export must be a separate child of the candidate directory")
    plan = json.loads((root / "plan.json").read_text())
    categories = {c["id"]: c for d in plan["domains"] for c in d["categories"]}
    db = sqlite3.connect("file:" + str(root / "collection.sqlite3") + "?mode=ro", uri=True)
    rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM images ORDER BY rowid")]
    db.close()
    row_ids = {r["id"] for r in rows}
    decisions, review_sources = {}, {}
    for folder in sorted((root / "visual-review").iterdir()):
        files = sorted(folder.glob("review*.json")) + sorted(folder.glob("scope-correction*.json"))
        for file in files:
            value = json.loads(file.read_text())
            for decision in value["decisions"]:
                identity = decision["id"]
                if identity not in row_ids:
                    raise ValueError("Review references an unknown candidate")
                if identity in decisions and not file.name.startswith("scope-correction"):
                    raise ValueError("Overlapping review decisions require an explicit correction record")
                decisions[identity] = decision
                review_sources[identity] = str(file.relative_to(root))
    counts, seen_bytes, seen_pixels, records = collections.Counter(), set(), set(), []
    destination.mkdir(parents=True, exist_ok=True)
    for row in rows:
        decision = decisions.get(row["id"])
        if not decision or decision["decision"] != "process_related_selected":
            continue
        identity = row["category_id"]
        limit = categories[identity].get("final_target", 50)
        eligible = decision.get("quota_eligible_for_search_tag", True)
        core = eligible and counts[identity] < limit
        if row["sha256"] in seen_bytes or row["pixel_sha256"] in seen_pixels:
            raise ValueError("Reviewed references contain exact duplicates")
        source = (root / row["file"]).resolve()
        if not source.is_relative_to(root) or hashlib.sha256(source.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Candidate file escaped the directory or changed")
        folder = destination / ("核心" if core else "补充") / row["industry_id"] / identity
        folder.mkdir(parents=True, exist_ok=True)
        image = folder / source.name
        caption, metadata = image.with_suffix(".txt"), image.with_suffix(".json")
        value = {**row, **{k: v for k, v in decision.items() if k not in ("id", "index")},
                 "file": str(image.relative_to(destination)),
                 "caption_file": str(caption.relative_to(destination)),
                 "metadata_file": str(metadata.relative_to(destination)),
                 "source_candidate_file": row["file"], "source_review_file": review_sources[row["id"]],
                 "collection_track": "process_core" if core else "process_supplement",
                 "selection_status": "assistant_visually_selected_relevance_only",
                 "selection_reason": decision["visible_topic_or_use"],
                 "relevance_status": "visually_reviewed_process_related",
                 "label_status": "source_caption_unverified", "technical_fact_review_status": "unreviewed",
                 "allow_training": False, "allow_public_redistribution": False, "allow_cloud_upload": False}
        for target, content in [(caption, row["caption"] + "\n"), (metadata, json.dumps(value, ensure_ascii=False, indent=2))]:
            if target.exists() and target.read_text() != content:
                raise ValueError("Existing export conflicts; choose a new destination")
        if not image.exists():
            shutil.copyfile(source, image)
        if hashlib.sha256(image.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Exported image checksum conflict")
        caption.write_text(row["caption"] + "\n")
        metadata.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        records.append(value)
        if core:
            counts[identity] += 1
        seen_bytes.add(row["sha256"])
        seen_pixels.add(row["pixel_sha256"])
    (destination / "总清单.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    report = {"status": "quota_complete" if all(counts[c] >= categories[c].get("final_target", 50) for c in categories) else "quota_incomplete",
              "candidate_images": len(rows), "reviewed_candidates": len(decisions),
              "pending_visual_review": len(row_ids - set(decisions)), "selected_images": len(records),
              "core_images": sum(counts.values()), "supplement_images": len(records) - sum(counts.values()),
              "category_counts": {c: counts[c] for c in categories},
              "remaining_per_category": {c: max(0, categories[c].get("final_target", 50) - counts[c]) for c in categories},
              "review_scope": "visible relevance only; source captions, engineering facts and licenses are not approved",
              "candidate_root": str(root)}
    (destination / "筛选结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.output, args.destination), ensure_ascii=False))
