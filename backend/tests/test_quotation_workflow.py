import sys
import os
import unittest
from fastapi.testclient import TestClient

# Ensure backend directory is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import test_support  # noqa: F401
from app import app, jobs
from extractor.pricing import get_component_price_candidates, estimate_reference_price
from db import db_get_job, db_save_job

client = TestClient(app)


class TestQuotationWorkflow(unittest.TestCase):
    def setUp(self):
        # Create a mock job for testing
        self.job_id = "test_quotation_job_001"
        self.mock_job = {
            "id": self.job_id,
            "job_id": self.job_id,
            "filename": "测试动力配电总箱图纸.dwg",
            "file_type": "dwg",
            "status": "done",
            "progress": 100,
            "summary": {
                "title": "测试工程配电箱报价表",
                "boxes_count": 2,
                "circuits_count": 4,
            },
            "data": {
                "boxes": [
                    {
                        "code": "01AP1",
                        "name": "动力配电箱 01AP1",
                        "type": "动力箱",
                        "size": "800×1200×250",
                        "location": "地下一层水泵房",
                        "quantity": 1
                    },
                    {
                        "code": "01AL1",
                        "name": "照明配电箱 01AL1",
                        "type": "照明箱",
                        "size": "400×300×160",
                        "location": "走廊",
                        "quantity": 2
                    }
                ],
                "circuits": [
                    {
                        "box": "01AP1",
                        "circuit_no": "1WL1",
                        "circuit_type": "feeder",
                        "breaker": "MCB-MA C16A/1P"
                    }
                ],
                "components": [
                    {
                        "name": "配电箱体",
                        "spec": "01AP1 800×1200×250",
                        "unit": "台",
                        "quantity": 1,
                        "used_in": "01AP1"
                    },
                    {
                        "name": "微型断路器",
                        "spec": "MCB-MA C16A/1P",
                        "unit": "只",
                        "quantity": 3,
                        "used_in": "01AP1"
                    },
                    {
                        "name": "电流互感器",
                        "spec": "50/5",
                        "unit": "只",
                        "quantity": 3,
                        "used_in": "01AP1"
                    },
                    {
                        "name": "浪涌保护器",
                        "spec": "SPD 4P Up≤2.5kV Iimp≥12.5kA",
                        "unit": "只",
                        "quantity": 1,
                        "used_in": "01AP1"
                    },
                    {
                        "name": "配电箱体",
                        "spec": "01AL1 400×300×160",
                        "unit": "台",
                        "quantity": 2,
                        "used_in": "01AL1"
                    },
                    {
                        "name": "微型断路器",
                        "spec": "MCB-MA C16A/1P",
                        "unit": "只",
                        "quantity": 6,
                        "used_in": "01AL1"
                    }
                ],
                "requirements": [],
                "uncertainties": []
            }
        }
        jobs[self.job_id] = self.mock_job
        db_save_job(self.mock_job)

    def test_reference_estimate_price(self):
        # 1. 配电箱体定额参考价：来源必须如实写“定额估算”，不能冒充联网行情
        box_cand = estimate_reference_price("配电箱体", "400×300×160")
        self.assertEqual(box_cand["price"], 165.0)
        self.assertIn("定额估算", box_cand["source"])
        self.assertNotIn("1688", box_cand["reference"])

        # 2. 电流互感器 50/5
        ct_cand = estimate_reference_price("电流互感器", "50/5")
        self.assertEqual(ct_cand["price"], 26.0)
        self.assertIn("50/5A", ct_cand["model"])

        # 3. 接触器 110A
        contactor_cand = estimate_reference_price("交流接触器", "110A")
        self.assertEqual(contactor_cand["price"], 228.0)

        # 4. 热继电器 5~7A
        thermal_cand = estimate_reference_price("热继电器", "5~7A")
        self.assertEqual(thermal_cand["price"], 36.0)

    def test_candidates_no_fake_thermal_relay(self):
        # 针对配电箱体，内置库未收录时，不能凭空出现施耐德热继电器
        cands = get_component_price_candidates("400×300×160", comp_name="配电箱体")
        self.assertIsNone(cands.get("info"))
        self.assertIsNone(cands.get("market"))
        self.assertIsNotNone(cands.get("ai_suggested"))
        self.assertEqual(cands["ai_suggested"]["price"], 165.0)

    def test_box_components_isolation(self):
        # 01AP1 中只能包含属于 01AP1 的元器件，严禁混入 01AL1 的器件或外壳
        resp = client.get(f"/api/jobs/{self.job_id}/quotation?brand=正泰")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        ap1_comps = [r for r in data["rows"] if r.get("box_code") == "01AP1" and r.get("type") == "component"]
        # 01AP1 应有 3 项器件 (微断, 互感器, 浪涌)
        self.assertEqual(len(ap1_comps), 3)
        specs = [c["design_spec"] for c in ap1_comps]
        self.assertIn("MCB-MA C16A/1P", specs)
        self.assertIn("50/5", specs)
        self.assertIn("SPD 4P Up≤2.5kV Iimp≥12.5kA", specs)
        # 不应包含 01AL1
        for c in ap1_comps:
            self.assertNotIn("01AL1", c["design_spec"])

    def test_quotation_override_persistence(self):
        # 模拟前端点击 [采用] AI 建议价并落盘
        override_payload = {
            "brand": "正泰",
            "overrides": {
                "01AP1_电流互感器_50/5": {
                    "unit_price": 26.0,
                    "price": 26.0,
                    "model": "BH-0.66 50/5A 0.5级 穿心式互感器",
                    "source": "AI建议价",
                    "brand": "正泰"
                }
            }
        }
        post_resp = client.post(f"/api/jobs/{self.job_id}/quotation", json=override_payload)
        self.assertEqual(post_resp.status_code, 200)

        # 检查持久化到 SQLite 后的回填
        get_resp = client.get(f"/api/jobs/{self.job_id}/quotation?brand=正泰")
        self.assertEqual(get_resp.status_code, 200)
        data = get_resp.json()
        ct_row = next(r for r in data["rows"] if r.get("box_code") == "01AP1" and r.get("design_spec") == "50/5")
        self.assertEqual(ct_row["unit_price"], 26.0)
        self.assertEqual(ct_row["source"], "AI建议价")
        self.assertEqual(ct_row["replacement_model"], "BH-0.66 50/5A 0.5级 穿心式互感器")

        # 再次直接从 SQLite WAL 加载验证
        saved_db_job = db_get_job(self.job_id)
        self.assertIn("01AP1_电流互感器_50/5", saved_db_job.get("quotation_overrides", {}))


if __name__ == "__main__":
    unittest.main()
