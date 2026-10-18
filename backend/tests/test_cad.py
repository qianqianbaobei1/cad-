# -*- coding: utf-8 -*-
"""CAD (DWG / DXF) 原生解析与渲染转换单元测试。"""

import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

import ezdxf
import pymupdf

from extractor.cad import (
    clean_mtext,
    extract_cad_entities,
    is_cad_path,
    process_cad_file,
)


class TestCADPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_is_cad_path(self):
        self.assertTrue(is_cad_path("drawing.dwg"))
        self.assertTrue(is_cad_path("DRAWING.DWG"))
        self.assertTrue(is_cad_path("/path/to/file.dxf"))
        self.assertTrue(is_cad_path("project.DXF"))
        self.assertFalse(is_cad_path("document.pdf"))
        self.assertFalse(is_cad_path("image.png"))
        self.assertFalse(is_cad_path("data.xlsx"))

    def test_clean_mtext(self):
        # 测试 AutoCAD MTEXT 换行与控制符清洗
        raw1 = r"AL1\P照明箱"
        self.assertEqual(clean_mtext(raw1), "AL1\n照明箱")

        raw2 = r"\fSimSun;MCB-63\P\C1;C16A/1P"
        self.assertEqual(clean_mtext(raw2), "MCB-63\nC16A/1P")

        raw3 = r"%%c200\P%%d45"
        self.assertEqual(clean_mtext(raw3), "Φ200\n°45")

        # 下划线开关不能污染位号；上标和未知控制码按配电箱文字原样处理
        self.assertEqual(clean_mtext("%%UV4102A"), "V4102A")
        self.assertEqual(clean_mtext(r"电缆%%c95\P250Nm%%133/min"), "电缆Φ95\n250Nm³/min")
        self.assertEqual(clean_mtext("%%P0.2MPa"), "±0.2MPa")
        self.assertEqual(clean_mtext("%%Q12"), "%%Q12")

    def test_dxf_entity_extraction_and_rendering(self):
        dxf_path = os.path.join(self.temp_dir, "test_sample.dxf")
        pdf_path = os.path.join(self.temp_dir, "test_sample.pdf")

        # 构造模拟 CAD 配电箱系统图 DXF
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("1AL1 照明配电箱", dxfattribs={"height": 25, "insert": (50, 500)})
        msp.add_text("进线开关: MCCB-63/63A/3P", dxfattribs={"height": 18, "insert": (50, 460)})
        msp.add_mtext("WL1: MCB-63/C16A/1P\\P0.8kW L1 走廊照明", dxfattribs={"char_height": 14, "insert": (50, 380)})
        msp.add_mtext("WL2: MCB-63/C16A/1P\\P1.2kW L2 展厅照明", dxfattribs={"char_height": 14, "insert": (50, 320)})
        doc.saveas(dxf_path)

        # 1. 实体提取验证
        entities = extract_cad_entities(dxf_path)
        self.assertGreaterEqual(len(entities), 4)

        # 检查排序：从上到下按 Y 降序排列
        y_coords = [e["y"] for e in entities]
        self.assertEqual(y_coords, sorted(y_coords, reverse=True))

        texts = [e["text"] for e in entities]
        self.assertTrue(any("1AL1" in t for t in texts))
        self.assertTrue(any("MCCB-63" in t for t in texts))
        self.assertTrue(any("WL1:" in t for t in texts))

        # 2. 渲染为 PDF 验证
        out_pdf, extracted = process_cad_file(dxf_path, pdf_path)
        self.assertTrue(os.path.exists(out_pdf))
        self.assertGreater(os.path.getsize(out_pdf), 1000)

        # 验证转换出的 PDF 可被 PyMuPDF 正常读取和光栅化
        pdf_doc = pymupdf.open(out_pdf)
        self.assertEqual(len(pdf_doc), 1)
        pix = pdf_doc[0].get_pixmap(dpi=100)
        self.assertGreater(pix.width, 200)
        self.assertGreater(pix.height, 200)
        pdf_doc.close()

    def test_surrogate_sanitization(self):
        from extractor.cad import sanitize_surrogates
        bad_text = "照明箱 \ud83d\ud83d 进线 \ud800 出线"
        cleaned = sanitize_surrogates(bad_text)
        # 确保清洗后可安全进行 UTF-8 编码与解码
        encoded = cleaned.encode("utf-8")
        self.assertIn("照明箱", cleaned)
        self.assertIn("出线", cleaned)
        self.assertNotIn("\ud800", cleaned)

        # clean_mtext 也应净化代理对
        mtext_cleaned = clean_mtext(r"AL1\P\ud83d测试%%c50")
        self.assertIn("AL1\n测试Φ50", mtext_cleaned)

    def test_construction_plan_guardrail(self):
        from unittest.mock import patch
        dxf_path = os.path.join(self.temp_dir, "pure_plan.dxf")
        pdf_path = os.path.join(self.temp_dir, "pure_plan.pdf")

        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for i in range(10):
            msp.add_text(f"二层照明平面施工图分区{i}", dxfattribs={"insert": (0, i * 100)})
        doc.saveas(dxf_path)

        with patch("extractor.cad.MAX_TEXT_ENTITIES_LIMIT", 5):
            with self.assertRaises(ValueError) as ctx:
                process_cad_file(dxf_path, pdf_path)
            self.assertIn("未检测到配电箱电气系统图", str(ctx.exception))

    def test_whole_sheet_preview_one_page_per_system_sheet(self):
        """预览按整张图幅出页：每张系统图一页，箱框外的电缆列不截断，平面图不出页。"""
        dxf_path = os.path.join(self.temp_dir, "sheets.dxf")
        out_pdf = os.path.join(self.temp_dir, "sheets.pdf")

        doc = ezdxf.new("R2010")
        frame_blk = doc.blocks.new("横式A0")
        frame_blk.add_lwpolyline([(0, 0), (118900, 0), (118900, 84100), (0, 84100)], close=True)
        msp = doc.modelspace()
        for i, zh in enumerate("一二"):
            msp.add_blockref("横式A0", insert=(i * 118900, 0))
            msp.add_text(f"配电系统图{zh}", dxfattribs={"height": 350, "insert": (i * 118900 + 100000, 2000)})
            for j in (1, 2):
                x, y = i * 118900 + 3000 + (j - 1) * 40000, 48000
                for k in range(4):
                    msp.add_text(f"WL{k + 1}", dxfattribs={"height": 200, "insert": (x + 500, y - k * 1500)})
                    msp.add_text("MCB-63/C16A/1P", dxfattribs={"height": 200, "insert": (x + 2000, y - k * 1500)})
                    msp.add_text("ZR-YJV-4x6", dxfattribs={"height": 200, "insert": (x + 26000, y - k * 1500)})
                msp.add_text(f"0{i + 1}AL{j} 照明配电箱", dxfattribs={
                    "height": 300, "insert": (x + 2000, y - 4 * 1500 - 600)})
        msp.add_blockref("横式A0", insert=(260000, 0))
        msp.add_text("一层照明平面图", dxfattribs={"height": 350, "insert": (362000, 2000)})
        msp.add_text("插座回路说明", dxfattribs={"height": 200, "insert": (300000, 40000)})
        doc.saveas(dxf_path)

        out, texts = process_cad_file(dxf_path, out_pdf)
        pdf = pymupdf.open(out)
        try:
            self.assertEqual(len(pdf), 2)
            self.assertEqual(sorted({t["sheet"] for t in texts}), ["配电系统图一", "配电系统图二"])
            self.assertEqual(sum("ZR-YJV-4x6" in t["text"] for t in texts), 16)
            self.assertFalse(any("插座回路说明" in t["text"] for t in texts))
        finally:
            pdf.close()

        from extractor.assemble import assemble
        from extractor.takeoff import extract_takeoff
        data = assemble(extract_takeoff(dxf_path)).model_dump()
        self.assertEqual(sorted(b["code"] for b in data["boxes"]), ["01AL1", "01AL2", "02AL1", "02AL2"])
        self.assertEqual(len(data["circuits"]), 16)


class TestDwgConversionGuard(unittest.TestCase):
    """DWG 转 DXF 只认本图产物，坏文件不能冒充另一张配电箱图。"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _panel_dxf(self, path: str, label: str) -> None:
        doc = ezdxf.new("R2010")
        doc.modelspace().add_text(label, dxfattribs={"height": 25, "insert": (50, 500)})
        doc.saveas(path)

    def _dwg(self, name: str, size: int) -> str:
        path = os.path.join(self.temp_dir, name)
        with open(path, "wb") as handle:
            handle.write(b"AC1015" + b"\x00" * max(0, size - 6))
        return path

    def test_structure_check_rejects_shell_and_accepts_panel_dxf(self):
        from extractor.cad_ledger import is_valid_dxf

        shell = os.path.join(self.temp_dir, "shell.dxf")
        with open(shell, "wb") as handle:
            handle.write(b"not a drawing" * 40)
        ok, why = is_valid_dxf(shell)
        self.assertFalse(ok)
        self.assertIn("SECTION", why)

        panel = os.path.join(self.temp_dir, "1AL.dxf")
        self._panel_dxf(panel, "1AL1 照明配电箱")
        self.assertTrue(is_valid_dxf(panel)[0])

    def test_tiny_dwg_fails_fast_and_does_not_borrow_neighbor(self):
        from extractor.cad import dwg_to_dxf

        neighbor = os.path.join(self.temp_dir, "other.dxf")
        self._panel_dxf(neighbor, "AP1 动力配电箱")
        tiny = self._dwg("fake.dwg", 64)
        target = os.path.join(self.temp_dir, "fake.dxf")
        with unittest.mock.patch("extractor.cad.find_dwg2dxf_tool") as finder:
            self.assertFalse(dwg_to_dxf(tiny, target))
            finder.assert_not_called()
        self.assertFalse(os.path.exists(target))
        kept = ezdxf.readfile(neighbor)
        texts = [entity.dxf.text for entity in kept.modelspace().query("TEXT")]
        self.assertEqual(texts, ["AP1 动力配电箱"])

    def test_invalid_converter_output_is_discarded(self):
        from extractor.cad import dwg_to_dxf

        dwg = self._dwg("panel.dwg", 4096)
        target = os.path.join(self.temp_dir, "panel.dxf")
        self._panel_dxf(target, "旧缓存里的别的箱子")

        def fake_run(cmd, **kwargs):
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as handle:
                handle.write(b"SECTION but not really" * 20)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with unittest.mock.patch("extractor.cad.find_dwg2dxf_tool", return_value="dwg2dxf"), \
             unittest.mock.patch("extractor.cad.subprocess.run", side_effect=fake_run):
            self.assertFalse(dwg_to_dxf(dwg, target))
        kept = ezdxf.readfile(target)
        texts = [entity.dxf.text for entity in kept.modelspace().query("TEXT")]
        self.assertEqual(texts, ["旧缓存里的别的箱子"])
        self.assertFalse(os.path.exists(target + ".converting"))

    def test_valid_output_replaces_only_its_own_file(self):
        from extractor.cad import dwg_to_dxf

        dwg = self._dwg("panel.dwg", 4096)
        target = os.path.join(self.temp_dir, "panel.dxf")
        neighbor = os.path.join(self.temp_dir, "neighbor.dxf")
        self._panel_dxf(neighbor, "AP1 动力配电箱")
        good = os.path.join(self.temp_dir, "good.dxf")
        self._panel_dxf(good, "1AL1 照明配电箱")
        with open(good, "rb") as handle:
            payload = handle.read()

        def fake_run(cmd, **kwargs):
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as handle:
                handle.write(payload)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with unittest.mock.patch("extractor.cad.find_dwg2dxf_tool", return_value="dwg2dxf"), \
             unittest.mock.patch("extractor.cad.subprocess.run", side_effect=fake_run):
            self.assertTrue(dwg_to_dxf(dwg, target))
        texts = [entity.dxf.text for entity in ezdxf.readfile(target).modelspace().query("TEXT")]
        neighbor_texts = [entity.dxf.text for entity in ezdxf.readfile(neighbor).modelspace().query("TEXT")]
        self.assertEqual(texts, ["1AL1 照明配电箱"])
        self.assertEqual(neighbor_texts, ["AP1 动力配电箱"])

    def test_resolve_ignores_same_name_cache(self):
        from extractor.cad import resolve_dwg_dxf

        dwg = self._dwg("照明系统图.dwg", 4096)
        decoy_dir = os.path.join(self.temp_dir, ".cad_cache")
        os.makedirs(decoy_dir, exist_ok=True)
        decoy = os.path.join(decoy_dir, "照明系统图.dxf")
        self._panel_dxf(decoy, "AP1 动力配电箱")
        good = os.path.join(self.temp_dir, "good.dxf")
        self._panel_dxf(good, "1AL1 照明配电箱")
        with open(good, "rb") as handle:
            payload = handle.read()

        def fake_run(cmd, **kwargs):
            out = cmd[cmd.index("-o") + 1]
            with open(out, "wb") as handle:
                handle.write(payload)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with unittest.mock.patch("extractor.cad.find_dwg2dxf_tool", return_value="dwg2dxf"), \
             unittest.mock.patch("extractor.cad.subprocess.run", side_effect=fake_run) as runner:
            resolved = resolve_dwg_dxf(dwg)
            again = resolve_dwg_dxf(dwg)
        self.assertNotEqual(os.path.abspath(resolved), os.path.abspath(decoy))
        self.assertEqual(os.path.abspath(resolved), os.path.abspath(again))
        self.assertEqual(runner.call_count, 1)
        texts = [entity.dxf.text for entity in ezdxf.readfile(resolved).modelspace().query("TEXT")]
        decoy_texts = [entity.dxf.text for entity in ezdxf.readfile(decoy).modelspace().query("TEXT")]
        self.assertEqual(texts, ["1AL1 照明配电箱"])
        self.assertEqual(decoy_texts, ["AP1 动力配电箱"])


if __name__ == "__main__":
    unittest.main()


