# -*- coding: utf-8 -*-
"""图幅识别（sheets.py）单元与回归测试：

覆盖：
1. 几何判别与纸张长宽比 (_paper_like, _merge_borders, _rect_points)
2. 幅面优选与边际实体密度收敛 (_pick_variant)
3. 图纸目录提取与端子接线表过滤 (build_catalog)
4. 图纸分类与盖章框过滤 (_classify, detect_sheets STAMP_WORDS)
5. cad.py 的 detect_drawing_frames 原生管道联动集成
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf
from extractor.cad import GeometryIndex, detect_drawing_frames
from extractor.native_takeoff import TextRecord
from extractor.sheets import (
    Sheet,
    _classify,
    _merge_borders,
    _paper_like,
    _pick_variant,
    _rect_points,
    build_catalog,
    detect_sheets,
    text_height_basis,
)


class MockIndex:
    def __init__(self, boxes: list[tuple[float, float, float, float]]):
        self.boxes = boxes

    def query(self, rect: tuple[float, float, float, float]) -> list[int]:
        rx0, ry0, rx1, ry1 = rect
        out = []
        for i, b in enumerate(self.boxes):
            if not (b[2] < rx0 or b[0] > rx1 or b[3] < ry0 or b[1] > ry1):
                out.append(i)
        return out


class TestSheetsGeometryAndClassification(unittest.TestCase):
    def test_rect_points(self):
        # 顺时针正交矩形
        pts = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0), (0.0, 0.0)]
        self.assertEqual(_rect_points(pts, tol=1.0), (0.0, 0.0, 100.0, 50.0))

        # 4 点未闭合
        pts4 = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        self.assertEqual(_rect_points(pts4, tol=1.0), (0.0, 0.0, 100.0, 50.0))

        # 非正交斜四边形
        skew = [(0.0, 0.0), (100.0, 10.0), (100.0, 60.0), (0.0, 50.0)]
        self.assertIsNone(_rect_points(skew, tol=1.0))

    def test_paper_like(self):
        # A1 纸张比例 841 / 594 ~= 1.416
        self.assertTrue(_paper_like((0, 0, 841, 594), min_side=100, max_aspect=3.0))
        # 极细长长条矩形 (aspect > 3.0)
        self.assertFalse(_paper_like((0, 0, 1000, 50), min_side=20, max_aspect=3.0))
        # 尺寸过小
        self.assertFalse(_paper_like((0, 0, 50, 40), min_side=100, max_aspect=3.0))

    def test_merge_borders(self):
        # 外框与内图框（边距很小）
        outer = (0.0, 0.0, 1000.0, 700.0)
        inner = (10.0, 10.0, 990.0, 690.0)
        merged = _merge_borders([outer, inner], ratio=0.05)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], outer)

    def test_classify(self):
        self.assertEqual(_classify("配电箱系统图一"), "system")
        self.assertEqual(_classify("变配电室系统图"), "system")
        self.assertEqual(_classify("动力配电平面图"), "plan")
        self.assertEqual(_classify("照明平面布置图"), "plan")
        self.assertEqual(_classify("主要设备材料表及设计说明"), "notes")
        self.assertEqual(_classify("施工图设计总说明"), "notes")
        self.assertEqual(_classify("水泵安装大样图"), "other")


class TestVariantSelectionMarginalDensity(unittest.TestCase):
    def test_marginal_density_stops_expansion_on_empty_margins(self):
        # 模拟 02-13 边界膨胀问题：
        # 真实幅面 A1 (0, 0, 800, 600)，面积 480,000，内含 80+ 个密集元器件
        # 伪变体 A0+1 (0, 0, 1800, 600)，面积 1,080,000，向右伸出 1000 单位的空白地带，仅切到隔壁图纸的 5 个孤立图元
        boxes = []
        for x in range(10, 790, 80):
            for y in range(10, 590, 60):
                boxes.append((float(x), float(y), float(x + 5), float(y + 5)))
        base_count = len(boxes)
        self.assertGreaterEqual(base_count, 80)

        # 在 x 范围 [850, 1750] 内放 5 个稀疏杂元
        for i in range(5):
            boxes.append((850.0 + i * 150.0, 200.0, 860.0 + i * 150.0, 210.0))

        index = MockIndex(boxes)
        var_small = (0.0, 0.0, 800.0, 600.0)
        var_large = (0.0, 0.0, 1800.0, 600.0)

        chosen = _pick_variant(index, [var_small, var_large], skip=set(), tol=1.0, catalog_hits=[])
        # 边际增量 5/base_count < 15%，边际密度极低，绝不可扩张至 var_large
        self.assertEqual(chosen, var_small)

    def test_marginal_density_allows_expansion_when_content_is_dense(self):
        # 若大变体内真实拥有充实图纸内容（图元增量 >= 15% 且边际密度 eff >= 0.35），则应支持扩展到大图幅
        boxes = []
        # 小框区域 (0, 0, 500, 500) 内放 50 个图元
        for x in range(20, 480, 50):
            for y in range(20, 480, 100):
                boxes.append((float(x), float(y), float(x + 10), float(y + 10)))
        base_count = len(boxes)

        # 扩展区域 [500, 1000] 内同样密布 50 个图元
        for x in range(520, 980, 50):
            for y in range(20, 480, 100):
                boxes.append((float(x), float(y), float(x + 10), float(y + 10)))

        index = MockIndex(boxes)
        var_small = (0.0, 0.0, 500.0, 500.0)
        var_large = (0.0, 0.0, 1000.0, 500.0)

        chosen = _pick_variant(index, [var_small, var_large], skip=set(), tol=1.0, catalog_hits=[])
        self.assertEqual(chosen, var_large)


class TestCatalogAndStampRejection(unittest.TestCase):
    def test_build_catalog_rejects_terminal_schedule(self):
        # 端子排 / 接线表：如 K1-1, K1-2, K1-3, K1-4 竖排，右侧全是接线去向（如“至PLC控制柜”、“由配电屏引来”）
        texts = [
            TextRecord("K1-1", 100.0, 1000.0, 3.5, "0", "TEXT"),
            TextRecord("至PLC柜", 120.0, 1000.0, 3.5, "0", "TEXT"),
            TextRecord("K1-2", 100.0, 990.0, 3.5, "0", "TEXT"),
            TextRecord("至水泵控制箱", 120.0, 990.0, 3.5, "0", "TEXT"),
            TextRecord("K1-3", 100.0, 980.0, 3.5, "0", "TEXT"),
            TextRecord("由低压配电屏引来", 120.0, 980.0, 3.5, "0", "TEXT"),
            TextRecord("K1-4", 100.0, 970.0, 3.5, "0", "TEXT"),
            TextRecord("接排烟风机", 120.0, 970.0, 3.5, "0", "TEXT"),
        ]
        catalog, table_ids = build_catalog(texts, h_basis=3.5)
        # 应被 40% 有效图纸标题关键词以及前缀过滤完全剔除
        self.assertEqual(len(catalog), 0)
        self.assertEqual(len(table_ids), 0)

    def test_build_catalog_accepts_valid_drawings(self):
        texts = [
            TextRecord("07S-02-01", 100.0, 1000.0, 3.5, "0", "TEXT"),
            TextRecord("低压配电系统图一", 120.0, 1000.0, 3.5, "0", "TEXT"),
            TextRecord("07S-02-02", 100.0, 990.0, 3.5, "0", "TEXT"),
            TextRecord("低压配电系统图二", 120.0, 990.0, 3.5, "0", "TEXT"),
            TextRecord("07S-02-03", 100.0, 980.0, 3.5, "0", "TEXT"),
            TextRecord("动力平面布置图", 120.0, 980.0, 3.5, "0", "TEXT"),
            TextRecord("07S-02-04", 100.0, 970.0, 3.5, "0", "TEXT"),
            TextRecord("电气设计说明及主要设备材料表", 120.0, 970.0, 3.5, "0", "TEXT"),
        ]
        catalog, table_ids = build_catalog(texts, h_basis=3.5)
        self.assertEqual(len(catalog), 4)
        self.assertIn("07S-02-01", catalog)
        self.assertEqual(catalog["07S-02-01"], "低压配电系统图一")
        self.assertEqual(catalog["07S-02-03"], "动力平面布置图")

    def test_detect_sheets_filters_stamps(self):
        # 构造带有出图章 / 审图章的独立图框
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        # 正常图框
        msp.add_lwpolyline([(0, 0), (8410, 0), (8410, 5940), (0, 5940), (0, 0)], dxfattribs={"closed": True})
        # 审图章框
        msp.add_lwpolyline([(10000, 0), (12000, 0), (12000, 1500), (10000, 1500), (10000, 0)], dxfattribs={"closed": True})

        texts = [
            # 正常图框内文字
            TextRecord("02-D-01", 8000.0, 500.0, 25.0, "0", "TEXT"),
            TextRecord("低压配电系统图", 8000.0, 700.0, 35.0, "0", "TEXT"),
            # 印章框内文字
            TextRecord("此处盖注册执业专用章", 11000.0, 700.0, 30.0, "0", "TEXT"),
        ]
        idx = GeometryIndex(doc)
        sheets = detect_sheets(doc, idx, texts)

        # 审图章应被过滤掉，只保留真实工程图幅
        names = [s.name for s in sheets]
        self.assertTrue(any("系统图" in n for n in names))
        self.assertFalse(any("此处盖" in n or "专用章" in n for n in names))


class TestCadPipelineSheetsIntegration(unittest.TestCase):
    def test_detect_drawing_frames_uses_sheets_detect(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (84100, 0), (84100, 59400), (0, 59400), (0, 0)], dxfattribs={"closed": True})
        msp.add_text("02-D-01", dxfattribs={"height": 300}).set_placement((80000, 2000))
        msp.add_text("配电系统图", dxfattribs={"height": 500}).set_placement((75000, 3500))

        idx = GeometryIndex(doc)
        frames = detect_drawing_frames(doc, idx)

        self.assertGreaterEqual(len(frames), 1)
        target = frames[0]
        self.assertEqual(target["number"], "02-D-01")
        self.assertIn("系统图", target["name"])
        self.assertEqual(target["kind"], "system")
        self.assertEqual(target["source"], "polyline")


if __name__ == "__main__":
    unittest.main()
