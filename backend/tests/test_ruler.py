# -*- coding: utf-8 -*-
"""L0 金标准必须能把抄录结果打成满分，并把漏项、串箱打成不通过。"""
import hashlib
import json
import os
import unittest

import pymupdf

from extractor.ruler import score_extraction

ROOT = os.path.join(os.path.dirname(__file__), "..", "fixtures", "ruler")


def _gold() -> dict:
    with open(os.path.join(ROOT, "L0-2SAL3.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _as_result(gold: dict) -> dict:
    box = dict(gold["box"])
    box["code"] = "2SAL3"
    circuits = []
    for row in gold["circuits"]:
        item = dict(row)
        item["box"] = "2SAL3"
        circuits.append(item)
    return {"boxes": [box], "circuits": circuits}


class RulerTests(unittest.TestCase):
    def test_drawing_matches_the_frozen_checksum_and_has_no_text_layer(self):
        gold = _gold()
        path = os.path.join(ROOT, gold["drawing"])
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        self.assertEqual(digest, gold["sha256"])
        doc = pymupdf.open(path)
        self.assertEqual(len(doc), 1)
        self.assertEqual(len(doc[0].get_text("words")), 0)
        doc.close()

    def test_transcript_scores_perfect(self):
        gold = _gold()
        scored = score_extraction(gold, _as_result(gold))
        self.assertTrue(scored["passed"], scored["misses"])
        self.assertEqual(scored["recognize"]["recall"], 1.0)
        self.assertEqual(scored["split"]["recall"], 1.0)
        self.assertEqual(scored["extract"]["recall"], 1.0)

    def test_missing_circuit_and_wrong_box_fail(self):
        gold = _gold()
        result = _as_result(gold)
        result["circuits"] = [row for row in result["circuits"] if row["circuit_no"] != "WL2"]
        moved = next(row for row in result["circuits"] if row["circuit_no"] == "WX1")
        moved["box"] = "2SAL2"
        scored = score_extraction(gold, result)
        self.assertFalse(scored["passed"])
        self.assertLess(scored["extract"]["recall"], 1.0)
        self.assertLess(scored["split"]["recall"], 1.0)
        self.assertTrue(any("WL2" in item or "缺少" in item for item in scored["misses"]))
        self.assertTrue(any("切分错误" in item and "WX1" in item for item in scored["misses"]))


if __name__ == "__main__":
    unittest.main()
