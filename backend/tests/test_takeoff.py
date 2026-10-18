# -*- coding: utf-8 -*-
"""原生读图引擎（extractor.takeoff）的回归测试。

金标准（fixtures/ruler）是人工逐条核对过的真实图纸，是这里的主回归；
合成图只守住几条版面规则：箱名配对、台数确认、表格式回路、进线识别。
"""
import json
import os
import shutil
import unittest

import ezdxf

from extractor.assemble import assemble
from extractor.ruler import score_extraction
from extractor.takeoff import extract_takeoff

ROOT = os.path.join(os.path.dirname(__file__), "..", "fixtures", "ruler")


def _gold(case: str) -> dict:
    with open(os.path.join(ROOT, case + ".json"), encoding="utf-8") as handle:
        return json.load(handle)


def _score(case: str) -> dict:
    gold = _gold(case)
    path = os.path.join(ROOT, gold["drawing"])
    if path.lower().endswith(".dwg") and not (shutil.which("dwg2dxf") or os.path.isdir(os.path.join(ROOT, "drawings", ".cad_cache"))):
        raise unittest.SkipTest("没有 dwg2dxf，也没有转换缓存")
    data = assemble(extract_takeoff(path)).model_dump()
    return score_extraction(gold, {"boxes": data["boxes"], "circuits": data["circuits"],
                                   "components": data["components"]})


class GoldDrawingTests(unittest.TestCase):
    def test_l8_boiler_room_system_diagram_passes(self):
        sc = _score("L8-配电系统图一")
        self.assertTrue(sc["passed"], sc["misses"][:5])

    def test_l7_site_plan_diagram_passes(self):
        sc = _score("L7-总图")
        self.assertTrue(sc["passed"], sc["misses"][:5])

    def test_l6_fire_control_room_boxes_and_circuits(self):
        """L6 箱柜识别、切分全对；唯一差异是箱体尺寸栏（图面写“XXM 照明配电箱”，金标准记为空）。"""
        sc = _score("L6-消控室")
        self.assertEqual(sc["recognize"]["recall"], 1.0)
        self.assertEqual(sc["recognize"]["precision"], 1.0)
        self.assertEqual(sc["split"]["recall"], 1.0)
        self.assertEqual(sc["split"]["precision"], 1.0)
        self.assertEqual([m for m in sc["misses"] if "size" not in m], [])


def _rows(msp, x: float, top: float, n: int, h: float = 200, pitch: float = 600) -> None:
    for k in range(n):
        y = top - k * pitch
        msp.add_text("MCB-C16A/1P", dxfattribs={"insert": (x, y), "height": h})
        msp.add_text(f"WL{k + 1}", dxfattribs={"insert": (x + 3000, y), "height": h})
        msp.add_text("BV-3x2.5 SC20", dxfattribs={"insert": (x + 5000, y), "height": h})
        msp.add_text("走廊照明", dxfattribs={"insert": (x + 9000, y), "height": h})


class LayoutRuleTests(unittest.TestCase):
    def test_two_boxes_side_by_side_keep_their_own_caption_and_rows(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for i, code in enumerate(("1AL1", "1AL2")):
            x = i * 30000
            msp.add_text(f"{code} 照明配电箱", dxfattribs={"insert": (x, 1000), "height": 300})
            _rows(msp, x, 0, 3 + i)
        raw = extract_takeoff(doc)
        per = {}
        for c in raw.circuits:
            per[c.box] = per.get(c.box, 0) + 1
        self.assertEqual(per, {"1AL1": 3, "1AL2": 4})
        self.assertEqual({b.name for b in raw.boxes}, {"照明配电箱"})

    def test_quantity_is_confirmed_only_when_the_drawing_says_so(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("1AL1 照明配电箱 共3台", dxfattribs={"insert": (0, 1000), "height": 300})
        _rows(msp, 0, 0, 3)
        msp.add_text("1AL2 照明配电箱", dxfattribs={"insert": (30000, 1000), "height": 300})
        _rows(msp, 30000, 0, 3)
        boxes = {b.code: b for b in extract_takeoff(doc).boxes}
        self.assertTrue(boxes["1AL1"].quantity_confirmed)
        self.assertEqual(boxes["1AL1"].quantity, 3)
        self.assertFalse(boxes["1AL2"].quantity_confirmed)
        self.assertEqual(boxes["1AL2"].quantity, 1)

    def test_scattered_breaker_text_without_a_box_is_not_a_box(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("图例：MCB-C16A/1P 表示微型断路器", dxfattribs={"insert": (0, 0), "height": 200})
        msp.add_text("C16A/1P", dxfattribs={"insert": (50000, 50000), "height": 200})
        raw = extract_takeoff(doc)
        self.assertEqual(raw.boxes, [])
        self.assertTrue(any("未读出配电箱系统图" in u.text for u in raw.uncertainties))


    def test_a_suffix_panel_code_is_not_mistaken_for_ampere_rating(self):
        """01AL2-1A 这类 A 型箱号不能被“1A=1安培”的电流规则误杀。"""
        from extractor.cad_extractor import iter_panel_codes
        self.assertEqual(iter_panel_codes("01AL2-1A 照明配电箱"), ["01AL2-1A"])
        self.assertEqual(iter_panel_codes("01AL2-3A 照明配电箱"), ["01AL2-3A"])
        self.assertEqual(iter_panel_codes("MCB-63A/1P"), [])
        self.assertEqual(iter_panel_codes("63A"), [])

    def test_long_caption_with_quantity_is_still_a_caption(self):
        """“共1台（壁挂式）”抬头超过 30 字，不能因此丢掉整台箱柜。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("1AL8 A型应急照明集中电源配电箱 共1台,（壁挂式）",
                     dxfattribs={"insert": (0, 1000), "height": 300})
        _rows(msp, 0, 0, 3)
        codes = {b.code for b in extract_takeoff(doc).boxes}
        self.assertIn("1AL8", codes)

    def test_inventory_supplements_a_confirmed_box_without_parsed_rows(self):
        """系统图标题确认了箱号、但版式没解析出回路时，补成待核对箱柜而不是丢掉。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("1AL9 照明配电箱", dxfattribs={"insert": (0, 1000), "height": 300})
        raw = extract_takeoff(doc)
        boxes = {b.code: b for b in raw.boxes}
        self.assertIn("1AL9", boxes)
        self.assertIn("待核对", boxes["1AL9"].note)
        self.assertEqual([c for c in raw.circuits if c.box == "1AL9"], [])

    def test_spare_lighting_is_a_load_not_a_spare_circuit_number(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("1AL1 照明配电箱", dxfattribs={"insert": (0, 1000), "height": 300})
        for k, load in enumerate(("走廊照明", "备用照明", "楼梯照明")):
            y = -k * 600
            msp.add_text("MCB-C16A/1P", dxfattribs={"insert": (0, y), "height": 200})
            msp.add_text(f"WL{k + 1}", dxfattribs={"insert": (3000, y), "height": 200})
            msp.add_text(load, dxfattribs={"insert": (9000, y), "height": 200})
        circuits = {c.circuit_no: c.load_name for c in extract_takeoff(doc).circuits}
        self.assertEqual(circuits, {"WL1": "走廊照明", "WL2": "备用照明", "WL3": "楼梯照明"})


if __name__ == "__main__":
    unittest.main()
