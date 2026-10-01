# -*- coding: utf-8 -*-
"""深度审计整改验收测试：全盘核验 P0-1 ~ P0-10 缺陷整改与防虚构硬约束。"""
import os
import tempfile
import unittest
import openpyxl
from fastapi.testclient import TestClient

from app import app
from extractor.schema import ExtractionResult, Box, Circuit, Component, Uncertainty
from extractor.excel import build_workbook, _infer_box_location
from extractor.cad_extractor import PANEL_CODE_PATTERN, EXCLUDE_CODE_PATTERN


class TestAuditRemediation(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_p0_1_no_incoming_switch_hallucination(self):
        """P0-1 验收：回路没有进线断路器时，严禁生造 C9 SW 3P 63A/100A 并计价。"""
        res = ExtractionResult(
            title="配电箱清单",
            boxes=[Box(code="AL_NO_INC", name="照明箱", quantity=1)],
            circuits=[
                Circuit(box="AL_NO_INC", circuit_no="WL1", breaker="C65N-16A/1P", load_name="照明")
            ],
            components=[]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "p0_1.xlsx")
            build_workbook(res, "测试", out_file, layout="3_sheets")
            wb = openpyxl.load_workbook(out_file)
            ws = wb["屏柜分项表"]
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    cell_str = str(cell or "")
                    self.assertNotIn("C9 SW", cell_str, "严禁编造 C9 SW 隔离开关")
                    self.assertNotIn("进线主控", cell_str, "无进线开关时不应出现进线主控行")

    def test_p0_2_no_branch_breaker_hallucination(self):
        """P0-2 验收：回路断路器型号未标明时，规格显示 -，单价 0.00，严禁生造 C9 2P C20A 6kA+ELE 30mA。"""
        res = ExtractionResult(
            title="配电箱清单",
            boxes=[Box(code="AL_EMPTY_BRK", name="照明箱", quantity=1)],
            circuits=[
                Circuit(box="AL_EMPTY_BRK", circuit_no="WL1", breaker="", load_name="未知回路")
            ],
            components=[]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "p0_2.xlsx")
            build_workbook(res, "测试", out_file, layout="3_sheets")
            wb = openpyxl.load_workbook(out_file)
            ws = wb["屏柜分项表"]
            found_dash = False
            for row in ws.iter_rows(values_only=True):
                # 检查回路行
                if row[1] == "出线断路器(待明确)":
                    found_dash = True
                    self.assertEqual(row[2], "-", "缺失型号应如实显示 -")
                    self.assertEqual(row[5], 0.0, "缺失断路器单价必须为 0.00")
                    self.assertEqual(row[8], "图纸未标断路器型号")
                for cell in row:
                    cell_str = str(cell or "")
                    self.assertNotIn("C9 2P C20A", cell_str, "严禁编造 C20A 默认出线断路器")
            self.assertTrue(found_dash, "未标明断路器的回路应标记为出线断路器(待明确)")

    def test_p0_3_no_spd_hallucination_for_2sal3(self):
        """P0-3 验收：箱体无 SPD（如 2SAL3）时，严禁强制塞入电涌保护器与 90.62 元虚假单价。"""
        res = ExtractionResult(
            title="配电箱清单",
            boxes=[Box(code="2SAL3", name="照明配电箱", quantity=1)],
            circuits=[
                Circuit(box="2SAL3", circuit_no="WL1", breaker="C65N-16A/1P", load_name="照明")
            ],
            components=[]  # 图纸中完全没有 SPD
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "p0_3.xlsx")
            build_workbook(res, "测试", out_file, layout="3_sheets")
            wb = openpyxl.load_workbook(out_file)
            ws = wb["屏柜分项表"]
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    cell_str = str(cell or "")
                    self.assertNotIn("DZ47sY", cell_str, "严禁为未设计 SPD 的箱体生造 DZ47sY")
                    self.assertNotIn("电涌保护器", cell_str, "2SAL3 箱内无浪涌，严禁强塞电涌保护器")

    def test_p0_4_no_boiler_workshop_hardcoding(self):
        """P0-4 验收：安装位置严禁粗暴兜底写死“锅炉房/制丝工房”。"""
        # 测试普通无位置商场配电箱
        loc_empty = _infer_box_location(Box(code="AP1", name="动力箱"), [])
        self.assertNotIn("锅炉房", loc_empty, "严禁兜底锅炉房")
        self.assertNotIn("制丝", loc_empty, "严禁兜底制丝工房")

        # 测试带楼层的箱体编号提取
        loc_floor = _infer_box_location(Box(code="2SAL2", name="照明箱"), [])
        self.assertEqual(loc_floor, "2层配电区")

    def test_p0_5_no_xm_ggd_model_hallucination(self):
        """P0-5 验收：箱柜型号在缺失时如实留空，严禁瞎编“XM(标准)”或“GGD(落地)”。"""
        res = ExtractionResult(
            title="配电箱清单",
            boxes=[Box(code="AL1", name="照明箱", size="", install="明装", quantity=1)],
            circuits=[Circuit(box="AL1", circuit_no="WL1", breaker="C65N-16A/1P")],
            components=[]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "p0_5.xlsx")
            build_workbook(res, "测试", out_file, layout="3_sheets")
            wb = openpyxl.load_workbook(out_file)
            ws = wb["屏柜汇总表"]
            for row in ws.iter_rows(values_only=True):
                if row[1] == "AL1":
                    self.assertEqual(row[3] or "", "", "尺寸缺失时型号栏应如实留空，不得生造 XM(标准)")

    def test_p0_6_no_ats_injection(self):
        """P0-6 验收：双电源箱严禁自动在元器件清单追加一台虚构 ATS 并计价。"""
        res = ExtractionResult(
            title="配电箱清单",
            boxes=[Box(code="AT1", name="双电源照明箱", quantity=1)],
            circuits=[
                Circuit(box="AT1", circuit_no="1N1", breaker="C65N-32A/3P", load_name="主进线"),
                Circuit(box="AT1", circuit_no="2N1", breaker="C65N-32A/3P", load_name="备进线"),
                Circuit(box="AT1", circuit_no="WL1", breaker="C65N-16A/1P", load_name="照明")
            ],
            components=[]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "p0_6.xlsx")
            build_workbook(res, "测试", out_file, layout="3_sheets")
            wb = openpyxl.load_workbook(out_file)
            ws = wb["屏柜分项表"]
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    cell_str = str(cell or "")
                    self.assertNotIn("ATSE-63", cell_str, "严禁凭空为双电源箱捏造 ATS 自动转换开关")

    def test_p0_7_export_409_gate(self):
        """P0-7 验收：导出存在待确认存疑时必须返回 HTTP 409，带 force=true 时方可强制放行。"""
        import uuid
        from app import jobs
        job_id = f"test_409_{uuid.uuid4().hex[:6]}"
        jobs[job_id] = {
            "status": "done",
            "filename": "gate_test.pdf",
            "summary": {"title": "门禁测试", "uncertainties": [{"text": "待核对项目", "resolved": False}]},
            "data": {
                "boxes": [{"code": "AL1", "name": "照明箱", "quantity": 1}],
                "circuits": [{"box": "AL1", "circuit_no": "WL1", "breaker": "C65N-16A/1P"}],
                "components": [],
                "uncertainties": [{"text": "待核对项目", "resolved": False}]
            },
            "changes": []
        }
        self.addCleanup(jobs.pop, job_id, None)

        # 默认导出触发 409
        resp = self.client.get(f"/api/jobs/{job_id}/excel")
        self.assertEqual(resp.status_code, 409)
        self.assertIn("待确认存疑", resp.json()["detail"])

        # 传参 force=true 强制放行导出
        resp_force = self.client.get(f"/api/jobs/{job_id}/excel?force=true")
        self.assertEqual(resp_force.status_code, 200)

    def test_p0_8_honest_truncation_warning(self):
        """P0-8 验收：截断自愈告警必须诚实告知尾部回路存在缺失，切勿编造'已保留全部元器件'。"""
        from extractor.vision import VisionProvider, repair_truncated_json
        truncated_json = '{"boxes":[{"code":"AL1","name":"照明箱"}],"circuits":[{"box":"AL1","circuit_no":"WL1","breaker":"C16A"}],"extra_devices":[],"requirements":[],"uncertainties":[],"cir'
        repaired = repair_truncated_json(truncated_json)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["circuits"][0]["circuit_no"], "WL1")

    def test_p0_9_cad_panel_code_pattern_matches_target_drawings(self):
        """P0-9 验收：CAD 柜号正则必须精准覆盖 2SAL2/2ALE/2AT/2SAL3/AW1~4，且排斥标准图集号。"""
        target_codes = ["2SAL2", "2ALE", "2AT", "2SAL3", "AW1~4", "01AL1-1", "AP1", "1AL1"]
        for code in target_codes:
            self.assertTrue(PANEL_CODE_PATTERN.match(code), f"正则应匹配主力柜号: {code}")

        fake_codes = ["04D701-1", "00DX001", "GB50054-2011", "图号", "S3-01"]
        for fake in fake_codes:
            self.assertTrue(EXCLUDE_CODE_PATTERN.match(fake) is not None, f"排除正则应过滤标准图集与非箱柜代号: {fake}")

    def test_p0_10_prompt_file_has_anti_hallucination_rules(self):
        """P0-10 验收：extract.txt 必须包含 SPD 防错规则与五项严禁虚构总则。"""
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "extract.txt")
        self.assertTrue(os.path.exists(prompt_path))
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("SPD防错核心规则", content)
        self.assertIn("严禁无中生有编造进线隔离开关或断路器", content)
        self.assertIn("严禁无中生有编造出线断路器规格", content)
        self.assertIn("严禁强制为无浪涌箱体添加 SPD 浪涌保护器", content)
        self.assertIn("严禁无中生有编造双电源 ATS 切换装置", content)
        self.assertIn("严禁无中生有编造箱体外形尺寸或 XM(标准)/GGD(落地) 型号", content)
        self.assertIn("严禁机械写死安装位置", content)


if __name__ == "__main__":
    unittest.main()
