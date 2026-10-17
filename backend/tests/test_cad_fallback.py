# -*- coding: utf-8 -*-
"""acadrust 兜底解析通道及双通道转换流水线单元测试。"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import ezdxf

from extractor.cad import dwg_to_dxf
from extractor.cad_fallback import (
    convert_acadrust,
    find_acadrust_tool,
    validate_document,
)
from extractor.cad_ledger import conversion_for


class TestCadFallback(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sample_dwg = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "../fixtures/ruler/drawings/03-扩建消控室-电气916(出图).dwg",
            )
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_minimal_dxf(self, path: str, text: str = "AL1 照明配电箱") -> str:
        doc = ezdxf.new("R2004")
        msp = doc.modelspace()
        msp.add_text(text, dxfattribs={"insert": (0, 0)})
        doc.saveas(path)
        return path

    def test_find_acadrust_tool(self):
        """测试能够自动发现已编译的 extractor-dwg 工具。"""
        tool = find_acadrust_tool()
        self.assertIsNotNone(tool, "未找到 extractor-dwg 可执行文件")
        self.assertTrue(os.path.isfile(tool))
        self.assertTrue(os.access(tool, os.X_OK))

    def test_validate_document(self):
        """测试 DXF 校验器能够准确区分合规图纸与异常文件。"""
        # 不存在的文件
        valid, _ = validate_document(os.path.join(self.temp_dir, "nonexistent.dxf"))
        self.assertFalse(valid)

        # 垃圾文本
        garbage = os.path.join(self.temp_dir, "garbage.dxf")
        with open(garbage, "w") as f:
            f.write("Not a DXF file")
        valid, _ = validate_document(garbage)
        self.assertFalse(valid)

        # 空图纸（无实体）
        empty_dxf = os.path.join(self.temp_dir, "empty.dxf")
        doc = ezdxf.new("R2004")
        doc.saveas(empty_dxf)
        valid, reason = validate_document(empty_dxf)
        self.assertFalse(valid)
        self.assertIn("没有可读空间实体", reason)

        # 包含正常文字的合规图纸
        good_dxf = os.path.join(self.temp_dir, "good.dxf")
        self._create_minimal_dxf(good_dxf, "AP1 动力箱")
        valid, reason = validate_document(good_dxf)
        self.assertTrue(valid, f"校验意外失败: {reason}")

    def test_convert_acadrust_real_sample(self):
        """测试使用内置真实 DWG 样本执行 acadrust 转换。"""
        self.assertTrue(os.path.isfile(self.sample_dwg), f"测试样本不存在: {self.sample_dwg}")
        target_dxf = os.path.join(self.temp_dir, "output.dxf")
        
        result = convert_acadrust(self.sample_dwg, target_dxf)
        
        self.assertTrue(result.get("accepted"), f"acadrust 转换未通过: {result.get('note')}")
        self.assertEqual(result.get("converter"), "acadrust")
        self.assertEqual(result.get("exit_code"), 0)
        self.assertTrue(os.path.isfile(target_dxf))
        self.assertGreater(os.path.getsize(target_dxf), 0)

        # 验证生成的 DXF 可以被 ezdxf 正常解析，且包含实体
        doc = ezdxf.readfile(target_dxf, encoding="utf-8")
        msp = doc.modelspace()
        self.assertGreater(len(msp), 1000, "生成的实体数量少于预期")

    def test_dwg_to_dxf_primary_success_skips_fallback(self):
        """当主通道 LibreDWG 成功时，不触发 acadrust 兜底。"""
        dwg_path = os.path.join(self.temp_dir, "test.dwg")
        dxf_path = os.path.join(self.temp_dir, "test.dxf")
        with open(dwg_path, "wb") as f:
            f.write(b"MOCK DWG DATA" * 100)
        self._create_minimal_dxf(dxf_path, "AP1 动力配电箱")

        with patch("extractor.cad._libredwg_to_dxf", return_value=True) as mock_libredwg, \
             patch("extractor.cad_fallback.convert_acadrust") as mock_fallback, \
             patch("extractor.cad_ledger.conversion_for", return_value={"tool": "dwg2dxf", "accepted": True}):
            success = dwg_to_dxf(dwg_path, dxf_path)
            self.assertTrue(success)
            mock_libredwg.assert_called_once()
            mock_fallback.assert_not_called()

    def test_dwg_to_dxf_fallback_on_primary_failure(self):
        """当主通道 LibreDWG 失败时，无缝切换至 acadrust 兜底通道。"""
        dwg_path = os.path.join(self.temp_dir, "test.dwg")
        dxf_path = os.path.join(self.temp_dir, "test.dxf")
        with open(dwg_path, "wb") as f:
            f.write(b"MOCK DWG DATA" * 100)

        def fake_convert(src, tgt):
            self._create_minimal_dxf(tgt, "兜底配电箱")
            return {
                "converter": "acadrust",
                "tool": "extractor-dwg",
                "exit_code": 0,
                "accepted": True,
                "output_nonempty": True,
                "dxf_valid": True,
                "warning_lines": 0,
                "error_lines": 0,
                "log_missing": False,
                "note": "acadrust 兜底转换通过",
            }

        with patch("extractor.cad._libredwg_to_dxf", return_value=False), \
             patch("extractor.cad_fallback.convert_acadrust", side_effect=fake_convert) as mock_fallback:
            success = dwg_to_dxf(dwg_path, dxf_path)
            self.assertTrue(success)
            mock_fallback.assert_called_once_with(dwg_path, dxf_path)
            
            # 验证转换账本记录了兜底事实
            record = conversion_for(dwg_path)
            self.assertIsNotNone(record)
            self.assertEqual(record.get("converter"), "acadrust")
            self.assertTrue(record.get("fallback_used"))
            self.assertEqual(len(record.get("attempts", [])), 2)


if __name__ == "__main__":
    unittest.main()
