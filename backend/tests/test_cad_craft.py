# -*- coding: utf-8 -*-
"""CADCraft 集成适配器单元测试。"""

import os
import shutil
import tempfile
import unittest

from extractor.cad_craft import (
    cadcraft_find,
    cadcraft_info,
    cadcraft_render_full,
    cadcraft_render_slice,
    find_cadcraft_tool,
)


class TestCadCraft(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sample_dxf = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "../fixtures/ruler/drawings/02-锅炉房-配电系统图一.dxf",
            )
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_find_cadcraft_tool(self):
        """测试可定位到已编译的 cadcraft-cli。"""
        tool = find_cadcraft_tool()
        self.assertIsNotNone(tool, "未找到 cadcraft-cli 可执行文件")
        self.assertTrue(os.path.isfile(tool))
        self.assertTrue(os.access(tool, os.X_OK))

    def test_cadcraft_info(self):
        """测试通过 cadcraft-cli 提取图纸元数据。"""
        info = cadcraft_info(self.sample_dxf)
        self.assertIsNotNone(info)
        self.assertIn("entityCount", info)
        self.assertGreater(info["entityCount"], 0)
        self.assertIn("layers", info)
        self.assertIn("extents", info)

    def test_cadcraft_find(self):
        """测试在图纸中按文本查找实体句柄。"""
        res = cadcraft_find(self.sample_dxf, "锅炉")
        self.assertIsNotNone(res)
        self.assertIn("matches", res)
        self.assertGreater(res.get("total", 0), 0)
        first_match = res["matches"][0]
        self.assertIn("handle", first_match)
        self.assertEqual(first_match["type"], "Text")

    def test_cadcraft_render_full_and_slice(self):
        """测试整图渲染与子窗口切片渲染。"""
        # 测试全图渲染
        full_png = os.path.join(self.temp_dir, "full.png")
        ok_full = cadcraft_render_full(self.sample_dxf, full_png)
        self.assertTrue(ok_full)
        self.assertTrue(os.path.isfile(full_png))
        self.assertGreater(os.path.getsize(full_png), 10000)

        # 测试基于 extents 的局部切片渲染
        slice_png = os.path.join(self.temp_dir, "slice.png")
        ok_slice = cadcraft_render_slice(
            self.sample_dxf,
            slice_png,
            (633215.0, 156551.0, 680000.0, 180000.0),
        )
        self.assertTrue(ok_slice)
        self.assertTrue(os.path.isfile(slice_png))
        self.assertGreater(os.path.getsize(slice_png), 10000)

    def test_cadcraft_inspect_and_vectors_chinese(self):
        """测试 CAD 实体属性审查与矢量流中文正确解码，无乱码与 surrogate 替代符。"""
        from extractor.cad_craft import cadcraft_get_vectors, cadcraft_inspect
        # 1. Inspect
        find_res = cadcraft_find(self.sample_dxf, "锅炉")
        self.assertTrue(find_res and find_res.get("matches"))
        h = find_res["matches"][0]["handle"]
        ins = cadcraft_inspect(self.sample_dxf, h)
        self.assertIsNotNone(ins)
        self.assertEqual(ins["handle"], h)

        # 2. Vectors
        vecs = cadcraft_get_vectors(self.sample_dxf, max_entities=1000)
        self.assertIn("texts", vecs)
        self.assertIn("layers", vecs)
        # 验证文本不含 lone surrogates 或大面积替代符
        for t in vecs["texts"]:
            txt = t[2]
            self.assertFalse(any("\udc80" <= ch <= "\udcff" for ch in txt))
            self.assertNotIn("\ufffd", txt)


if __name__ == "__main__":
    unittest.main()
