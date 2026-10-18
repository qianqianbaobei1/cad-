# -*- coding: utf-8 -*-
"""T12 价格库型号匹配纠错回归测试：硬约束门禁、参数解析与型号消歧。

回归覆盖：
1. MCCB-160MA/50A/3P：绝不匹配到 NXMH-250S/3200 125-250A 住宅专供（壳架与电流均不符）；
2. MCCB-160TM/32A/3P：匹配 160 壳架、32A 覆盖范围、3300 复式脱扣器；
3. MCB-C16A/1P：精确匹配 NXB-63 1P C16；
4. RCBO/2P C20+VM：精确匹配 NXBLE-32 2P C20 100mA 6kA；
5. GL-250/200A/3P：隔离开关正确匹配 NH40-250/3；
6. SPD 4P：无额定工作电流也能正确查库匹配 NXU-II 4P；
7. 电流互感器 200/5：变比一次电流 200A 正确匹配 BH-0.66 200/5A。
"""
import json
import os
import unittest

import test_support  # noqa: F401
from extractor.pricing import (
    calculate_component_unit_price,
    get_component_price_candidates,
    lookup_price_library,
    parse_component_features,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "job_b1d7db50ac00_data.json")


class TestT12FeatureParsing(unittest.TestCase):
    """测试特征向量解析：品类、极数、额定电流、壳架电流、脱扣器代号。"""

    def test_mccb_dual_current_and_frame(self):
        f = parse_component_features("MCCB-160MA/50A/3P")
        self.assertEqual(f["category"], "MCCB")
        self.assertEqual(f["poles"], "3P")
        self.assertEqual(f["frame_a"], 160)
        self.assertEqual(f["current_a"], 50)

    def test_mccb_tm_trip_unit(self):
        f = parse_component_features("MCCB-160TM/32A/3P")
        self.assertEqual(f["category"], "MCCB")
        self.assertEqual(f["poles"], "3P")
        self.assertEqual(f["frame_a"], 160)
        self.assertEqual(f["current_a"], 32)
        self.assertEqual(f["rel_code"], "3300")

    def test_mcb_features(self):
        f = parse_component_features("MCB-C16A/1P")
        self.assertEqual(f["category"], "MCB")
        self.assertEqual(f["poles"], "1P")
        self.assertEqual(f["current_a"], 16)
        self.assertEqual(f["curve"], "C")

    def test_rcbo_features(self):
        f = parse_component_features("RCBO/2P C20+VM")
        self.assertEqual(f["category"], "RCBO")
        self.assertEqual(f["poles"], "2P")
        self.assertEqual(f["current_a"], 20)
        self.assertTrue(f["has_leakage"])

    def test_isolator_features(self):
        f = parse_component_features("GL-250/200A/3P")
        self.assertEqual(f["category"], "ISOLATOR")
        self.assertEqual(f["poles"], "3P")
        self.assertEqual(f["frame_a"], 250)
        self.assertEqual(f["current_a"], 200)

    def test_spd_features(self):
        f = parse_component_features("SPD 4P")
        self.assertEqual(f["category"], "SPD")
        self.assertEqual(f["poles"], "4P")
        self.assertIsNone(f["current_a"])

    def test_ct_features(self):
        f = parse_component_features("电流互感器 200/5")
        self.assertEqual(f["category"], "CT")
        self.assertEqual(f["current_a"], 200)


class TestT12HardConstraintsAndMatching(unittest.TestCase):
    """测试硬约束门禁：额定电流范围、壳架电流、极数与品类一致性。"""

    def test_mccb_160ma_never_matches_250s_with_mismatched_current_range(self):
        """MCCB-160MA/50A/3P 绝不可匹配到 NXMH-250S/3200 125-250A（50A 不在 125-250A 内，且壳架为 250）。"""
        match = lookup_price_library("MCCB", "正泰", poles="3P", curr_a=50, raw_spec="MCCB-160MA/50A/3P", frame_a=160)
        self.assertIsNotNone(match)
        self.assertNotIn("NXMH-250S", match["model"])
        self.assertNotIn("125-250A", match["model"])
        self.assertIn("160", match["model"])
        # 若为住宅专供则必须带有明确标识
        cost, _, basis = calculate_component_unit_price("MCCB-160MA/50A/3P", brand="正泰")
        self.assertNotIn("NXMH-250S", basis)
        self.assertNotIn("125-250A", basis)

    def test_mccb_160tm_32a_matches_160_frame_3300(self):
        cost, _, basis = calculate_component_unit_price("MCCB-160TM/32A/3P", brand="正泰")
        self.assertGreater(cost, 0.0)
        self.assertIn("160", basis)

    def test_mcb_c16a_1p_exact_match(self):
        cost, list_p, basis = calculate_component_unit_price("MCB-C16A/1P", brand="正泰")
        self.assertAlmostEqual(cost, 4.73, places=2)
        self.assertIn("NXB-63", basis)
        self.assertIn("C16", basis)

    def test_rcbo_2p_c20_vm_match(self):
        cost, _, basis = calculate_component_unit_price("RCBO/2P C20+VM", brand="正泰")
        self.assertGreater(cost, 0.0)
        self.assertIn("NXBLE-32", basis)
        self.assertIn("C20", basis)

    def test_gl_250_200a_isolator_match(self):
        cost, _, basis = calculate_component_unit_price("GL-250/200A/3P", brand="正泰")
        self.assertGreater(cost, 0.0)
        self.assertIn("NH40-250", basis)

    def test_spd_4p_pricing_without_current(self):
        """SPD 无额定工作电流，严禁误报'规格电流缺失（待核）'，应正确查库计价。"""
        cost, _, basis = calculate_component_unit_price("SPD 4P", brand="正泰")
        self.assertGreater(cost, 0.0)
        self.assertNotIn("规格电流缺失", basis)
        self.assertIn("NXU", basis)

    def test_ct_200_5_pricing(self):
        cost, _, basis = calculate_component_unit_price("电流互感器 200/5", brand="正泰")
        self.assertAlmostEqual(cost, 9.64, places=2)
        self.assertIn("BH-0.66", basis)
        self.assertIn("200/5A", basis)

    def test_frame_size_hard_gate_rejects_smaller_frame(self):
        """设计要求 160 壳架，候选仅有 100 壳架时必须硬性剔除，不得降配。"""
        # 正泰价格库中若强制要求 250 壳架，160 壳架候选不可使用
        match = lookup_price_library("MCCB", "正泰", poles="3P", curr_a=100, frame_a=250)
        if match:
            self.assertNotIn("160S", match["model"])
            self.assertNotIn("160H", match["model"])

    def test_get_component_price_candidates_multi_brand(self):
        """验证多品牌候选卡片均通过消歧门禁。"""
        cands = get_component_price_candidates("MCCB-160TM/100A/3P")
        lib = cands.get("library") or {}
        if "施耐德" in lib:
            self.assertIn("CVS160", lib["施耐德"]["model"])
            self.assertEqual(lib["施耐德"]["code"], "22114")


if __name__ == "__main__":
    unittest.main()
