import json
import tempfile
import unittest
from pathlib import Path

from geonow.metrics import summarize
from geonow.reports import write_report
from geonow.storage import Store
from geonow.models import MonitorConfig


class MetricsTest(unittest.TestCase):
    def test_valid_denominator_and_sources_are_separate(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "x.sqlite3")
            run = store.new_run("b1", "web", "doubao", {})
            base = {"run_id": run, "batch_id": "b1", "sample_no": 1, "question_id": "q1", "question_text": "q", "provider": "doubao", "source": "web"}
            store.insert_sample({**base, "status": "success", "valid": True, "answer_text": "推荐我的品牌，见 https://example.com/a", "citations": [{"url": "https://example.com/a"}]})
            store.insert_sample({**base, "sample_no": 2, "status": "failed", "valid": False, "error": "timeout"})
            run2 = store.new_run("b1", "api", "model", {})
            store.insert_sample({**base, "run_id": run2, "provider": "model", "source": "api", "status": "success", "valid": True, "answer_text": "竞品A"})
            result = summarize(store.rows(), ["我的品牌"], ["竞品A"], ["https://example.com/a"])
            web = next(x for x in result["groups"] if x["source"] == "web")
            self.assertEqual(web["valid_sample_count"], 1)
            self.assertEqual(web["failed_sample_count"], 1)
            self.assertEqual(web["brand_mention_rate"], 1.0)
            self.assertEqual(len([x for x in result["groups"] if x["source"] == "api"]), 1)

    def test_report_mentions_limits(self):
        cfg = MonitorConfig.from_dict({"brand": "我的品牌", "competitors": ["竞品A"], "questions": ["q"]})
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "x.sqlite3")
            run = store.new_run("b", "web", "doubao", {})
            store.insert_sample({"run_id": run, "batch_id": "b", "sample_no": 1, "question_id": "q1", "question_text": "q", "provider": "doubao", "source": "web", "valid": True, "status": "success", "answer_text": "我的品牌"})
            out = write_report(store.rows(), cfg, Path(td) / "report.md")
            self.assertIn("不能把全部变化直接归因于合作方", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
