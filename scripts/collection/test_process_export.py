"""Check that raw candidate counts cannot masquerade as reviewed process quotas."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_reviewed_process import export
import test_image_store as fixtures


class ProcessExportTests(unittest.TestCase):
    def setUp(self):
        f = fixtures.CollectionTests()
        f.setUp()
        self.root, self.store = f.root, f.store
        domain = {"id": "materials", "manufacturing_type": "process"}
        category = {"id": "material_a", "name": "test", "target": 4, "final_target": 1}
        for colour in ("red", "green", "blue", "yellow"):
            self.store.accept(domain, category, f.item("Original " + colour), f.result(f.png(colour)))
        self.rows = self.store.records()
        (self.root / "plan.json").write_text(json.dumps({"domains": [{**domain, "categories": [category]}]}))
        self.review_dir = self.root / "visual-review/material_a"
        self.review_dir.mkdir(parents=True)
        decisions = [
            {"id": self.rows[0]["id"], "decision": "process_related_selected", "quota_eligible_for_search_tag": False, "visible_topic_or_use": "Other process material"},
            {"id": self.rows[1]["id"], "decision": "process_related_selected", "visible_topic_or_use": "Visible test process"},
            {"id": self.rows[3]["id"], "decision": "not_selected"},
        ]
        (self.review_dir / "review.json").write_text(json.dumps({"decisions": decisions}))

    def test_only_explicit_eligible_review_satisfies_quota(self):
        target = self.root / "selected"
        report = export(self.root, target)
        self.assertEqual(report["status"], "quota_complete")
        self.assertEqual((report["core_images"], report["supplement_images"], report["pending_visual_review"]), (1, 1, 1))
        rows = [json.loads(s) for s in (target / "总清单.jsonl").read_text().splitlines()]
        self.assertEqual({r["caption"] for r in rows}, {"Original red", "Original green"})
        for r in rows:
            self.assertEqual((target / r["caption_file"]).read_text(), r["caption"] + "\n")
            self.assertFalse(r["allow_training"])
        self.assertEqual(export(self.root, target), report)
        self.assertEqual(len(self.store.records()), 4)

    def test_tampered_candidate_is_rejected(self):
        (self.root / self.rows[0]["file"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            export(self.root, self.root / "selected")


if __name__ == "__main__":
    unittest.main()
