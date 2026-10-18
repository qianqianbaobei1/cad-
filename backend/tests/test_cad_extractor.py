# -*- coding: utf-8 -*-
"""cad_extractor 词法层（回路号、相序、断路器、电缆、箱号语法）与预览定位框的单元测试。

版面级的箱柜/回路识别由 extractor.takeoff 负责，见 test_takeoff.py。"""
import unittest

from extractor.cad_extractor import (
    RE_BREAKER,
    RE_BREAKER_FALLBACK,
    RE_CIRCUIT_NO,
    RE_PHASE,
    _is_spd_callout,
    _looks_like_cable,
    _row_device_field,
    assign_preview_bboxes,
    extract_panel_code,
)
from extractor.schema import Box, Circuit


class TestCadExtractor(unittest.TestCase):
    def test_regex_patterns(self):
        # 1. Circuit numbers
        self.assertTrue(RE_CIRCUIT_NO.match("N1"))
        self.assertTrue(RE_CIRCUIT_NO.match("WL2"))
        self.assertTrue(RE_CIRCUIT_NO.match("1WL1"))
        self.assertTrue(RE_CIRCUIT_NO.match("B1-WP2"))
        self.assertTrue(RE_CIRCUIT_NO.match("2SAL3-WL1"))
        self.assertTrue(RE_CIRCUIT_NO.match("PY-1-P1"))
        self.assertTrue(RE_CIRCUIT_NO.match("BF-2A-P1"))
        self.assertFalse(RE_CIRCUIT_NO.match("SC25"))
        # 端子号、点位号不是回路：单字母回路不能跟在任意横线前缀后，楼层前缀只留一到两位。
        self.assertFalse(RE_CIRCUIT_NO.match("S-1c-C1"))
        self.assertFalse(RE_CIRCUIT_NO.match("925P01"))

        # 2. Breakers
        self.assertTrue(RE_BREAKER.search("MCB-C16A/1P"))
        self.assertTrue(RE_BREAKER.search("RCBO/2P C20+VM(30mA,瞬时)"))
        self.assertTrue(RE_BREAKER.search("MCCB-100MA/32A/3P"))
        self.assertTrue(RE_BREAKER.search("ATS 63A/4P"))
        self.assertTrue(RE_BREAKER.search("iC65N-C16/1P"))
        self.assertTrue(RE_BREAKER_FALLBACK.search("C16A/1P"))
        # 设备位号末尾的 -D1 不是断路器极数。
        self.assertFalse(RE_BREAKER_FALLBACK.search("3/5TY-D1"))
        self.assertFalse(RE_BREAKER.search("3/5TY-D1"))
        # 曲线字母后面再跟短横线是设备位号，不是断路器额定值。
        self.assertFalse(RE_BREAKER_FALLBACK.search("B1-05"))
        self.assertFalse(RE_BREAKER_FALLBACK.search("B1-02"))
        self.assertTrue(RE_BREAKER_FALLBACK.search("C16A/1P"))

        # 相序可以重复写 L：L1/L2/L3、L1,L2,L3 与 L123、L1,N,PE 同一类。
        self.assertTrue(RE_PHASE.match("L1"))
        self.assertTrue(RE_PHASE.match("L123"))
        self.assertTrue(RE_PHASE.match("L1,2,3"))
        self.assertTrue(RE_PHASE.match("L1/L2/L3"))
        self.assertTrue(RE_PHASE.match("L1,L2,L3"))
        self.assertTrue(RE_PHASE.match("L1,N,PE"))
        self.assertTrue(RE_PHASE.match("L2,N,PE"))
        self.assertFalse(RE_PHASE.match("PE"))
        self.assertFalse(RE_PHASE.match("WL1"))
        self.assertFalse(RE_PHASE.match("照明"))

        # 3. Cables vs Breakers
        self.assertTrue(_looks_like_cable("WDZ-YJY-5x10-CT/JDG40-WC,FC"))
        self.assertFalse(_looks_like_cable("MCCB-63L/40A/4300-30mA"))
        self.assertTrue(_is_spd_callout("CPM-R40T"))
        self.assertTrue(_is_spd_callout("SPD 4P"))
        self.assertTrue(_is_spd_callout("I级试验电涌保护器:"))
        self.assertFalse(_is_spd_callout("ISCB2 65H2+IPRU 40r"))
        self.assertEqual(_row_device_field("LC1-D32C"), "contactor")
        self.assertEqual(_row_device_field("LRD-16C"), "thermal")
        self.assertEqual(_row_device_field("LDR-21C"), "thermal")
        self.assertEqual(_row_device_field("BH-0.66-100/5 /0.5"), "ct")
        self.assertIsNone(_row_device_field("MCB-C16A/1P"))
        self.assertIsNone(_row_device_field("WDZ-YJY-5x6"))
        self.assertTrue(_looks_like_cable("ZR-BV-450/750V-3x4.0 SC25 FC WC"))
        self.assertTrue(_looks_like_cable("WDZN-YJV-0.6/1kV-5x16 SC50"))

    def test_panel_code_grammar(self):
        """箱号语法是通用多项目语法：各式前缀、楼层号、并排箱体都认，图号、图集号、规格型号一律不认。"""
        for text, code in [
            ("01AC1-1 插座配电箱系统图", "01AC1-1"),
            ("消防01ATPY03配电箱系统图", "01ATPY03"),
            ("配电箱 C01", "C01"),
            ("配电箱 C08", "C08"),
            ("02AK01 控制箱", "02AK01"),
            ("2SAL2 照明配电箱", "2SAL2"),
            ("AW1/2/3/4-CDZ 充电桩端子箱", "AW1/2/3/4-CDZ"),
            ("1AL1 照明箱", "1AL1"),
            ("GQ1 消防水泵柜", "GQ1"),
            ("CD1 消防泵箱", "CD1"),
            ("XFDT-AT 消防电梯配电箱", "XFDT-AT"),
            ("WDDT-AT 电梯配电箱", "WDDT-AT"),
            ("9KX3 排风机控制箱", "9KX3"),
            ("10RDAL", "10RDAL"),
            ("B1ATPY1 车库排烟风机配电箱", "B1ATPY1"),
        ]:
            self.assertEqual(extract_panel_code(text), code, text)
        for text in (
            "07S20230052SD-01B-01",
            "TSW230152SD-DZ-06",
            "03D702-3",
            "07SD101-8",
            "图集01-1",
            "63A",
            "10KW",
            "220V",
            "50HZ",
            "600MM",
            "IP65",
            "SC25",
            "LC1-D09C",
            "LC1-D40AC",
            "LC1-F115",
            "ESB20",
            "HND07E3Y/K",
            "HND09E3Y/K",
            "PC20",
            "5-PC20-WC",
            "10P20",
            "ATSE-63A/4P",
            "XLP000-25A/3P",
            "AC220V",
            "WDZ-YJY-5x16-CT/SC50-WC,FC",
            "WL1",
            "C20A",
            "1X",
            "40R",
            "65H2",
            "TMY-4X",
        ):
            self.assertIsNone(extract_panel_code(text), text)

    def test_preview_bbox_follows_cad_text(self):
        boxes = [Box(code="1AL1", name="照明配电箱")]
        circuits = [Circuit(box="1AL1", circuit_no="WL1")]
        texts = [
            {"text": "图框", "x": 0, "y": 0, "page": 3},
            {"text": "图框", "x": 1000, "y": 1000, "page": 3},
            {"text": "1AL1", "x": 100, "y": 800, "page": 3},
            {"text": "WL1", "x": 400, "y": 500, "page": 3},
        ]
        assign_preview_bboxes(boxes, circuits, texts, {"1AL1": 3})
        self.assertEqual(boxes[0].bbox.page, 3)
        self.assertLess(boxes[0].bbox.w, 0.5)
        self.assertEqual(circuits[0].bbox.page, 3)
        self.assertNotEqual((circuits[0].bbox.x, circuits[0].bbox.y), (boxes[0].bbox.x, boxes[0].bbox.y))


if __name__ == "__main__":
    unittest.main()
