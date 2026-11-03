"""Persist explicit image-by-image scope decisions after inspecting numbered sheets.

This never infers selections from captions. Input groups contain reviewer-supplied
indices and observations. A selection is relevance review, not technical approval.
"""
import argparse
import json
from pathlib import Path


def record(root, category, groups, *, prefix="review", start=1, end=None):
    folder = Path(root) / "visual-review" / category
    rows = json.loads((folder / "rows.json").read_text())
    end = len(rows) if end is None else end
    decisions = {}
    for group in groups:
        for index in group["indices"]:
            if index in decisions or not start <= index <= end:
                raise ValueError("Duplicate or out-of-range reviewed index")
            decisions[index] = {
                "index": index, "id": rows[index - 1]["id"],
                "decision": group.get("decision", "process_related_selected"),
                "process_role": group["role"],
                "visible_topic_or_use": group["observation"],
                "quota_eligible_for_search_tag": group.get("quota_eligible", True),
                "label_status": "source_caption_unverified",
                "technical_fact_review_status": "unreviewed",
            }
    for index in range(start, end + 1):
        decisions.setdefault(index, {
            "index": index, "id": rows[index - 1]["id"], "decision": "not_selected",
            "process_role": "excluded_from_process_core",
            "visible_topic_or_use": "已看图；产品广告、封面、下游装配加工、无关主题、近似重复或清晰度不足，不计入流程核心资料",
            "quota_eligible_for_search_tag": False,
            "label_status": "source_caption_unverified", "technical_fact_review_status": "unreviewed",
        })
    selected = sum(d["decision"] == "process_related_selected" for d in decisions.values())
    eligible = sum(d["decision"] == "process_related_selected" and d["quota_eligible_for_search_tag"] for d in decisions.values())
    result = {"category_id": category, "review_kind": "assistant_visual_scope_review_not_engineering_fact_validation",
              "candidate_images": end - start + 1, "selected_process_related": selected,
              "quota_eligible_selected": eligible, "final_target": 50,
              "remaining_needed": max(0, 50 - eligible),
              "decisions": [decisions[i] for i in sorted(decisions)]}
    target = folder / (prefix + ".json")
    if target.exists() and json.loads(target.read_text()) != result:
        raise ValueError("Review exists; use a new prefix to preserve previous decisions")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return {k: v for k, v in result.items() if k != "decisions"}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--groups", type=Path, required=True)
    p.add_argument("--prefix", default="review")
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--end", type=int)
    a = p.parse_args()
    print(json.dumps(record(a.output, a.category, json.loads(a.groups.read_text()), prefix=a.prefix, start=a.start, end=a.end), ensure_ascii=False))
