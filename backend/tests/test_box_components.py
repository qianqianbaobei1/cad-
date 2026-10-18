# -*- coding: utf-8 -*-
"""T17/T13 回归：元器件按柜拆分同源，控制电缆回路不进断路器组价。

T17：data.components 是项目级汇总，整行 quantity 是全项目总数。旧组价只判断
"used_in 里有没有本柜"就把整行交给本柜，锅炉房真实任务因此把 232 件算成 757 件。
T13：ZR-KYJVP 控制电缆回路（至现场操作柱/设备PLC柜）breaker 为空，不能被补成
"分支断路器"，也不该计入断路器回路数与出线组装工时。
"""
import json
import os
import unittest
import uuid

import test_support  # noqa: F401
from fastapi.testclient import TestClient

from app import app as fastapi_app, jobs
from db import db_save_job
from extractor.box_components import (
    UNASSIGNED,
    component_total,
    split_components_by_box,
)
from extractor.pricing import calculate_box_quotation

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "job_b1d7db50ac00_data.json")


def _snapshot() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _mk_job(data: dict) -> str:
    job_id = "t17_" + uuid.uuid4().hex[:8]
    jobs[job_id] = {
        "job_id": job_id,
        "filename": "按柜拆分回归.dwg",
        "file_type": "dwg",
        "status": "done",
        "project": "回归项目",
        "summary": {"title": "按柜拆分回归"},
        "data": data,
        "changes": [],
    }
    db_save_job(jobs[job_id])
    return job_id


def _box_rows(rows_by_box: dict, code: str) -> list[dict]:
    return rows_by_box.get(code) or []


def _quantities(rows: list[dict]) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["name"], row["spec"])
        out[key] = out.get(key, 0) + int(row["quantity"])
    return out


class TestSplitComponentsByBox(unittest.TestCase):
    def test_跨柜一行按片段数拆开且总量守恒(self):
        data = {
            "boxes": [{"code": "1AP1"}, {"code": "1AP2"}, {"code": "1AP3"}],
            "circuits": [],
            "components": [
                {"name": "塑壳断路器", "spec": "MCCB-100/50A/3P", "unit": "只", "quantity": 6,
                 "used_in": "1AP1 D01、1AP1 D02、1AP2 D01、1AP2 D02、1AP3 D01、1AP3 D02"},
            ],
        }
        split = split_components_by_box(data)
        for code in ("1AP1", "1AP2", "1AP3"):
            self.assertEqual([row["quantity"] for row in split[code]], [2], code)
        self.assertEqual(component_total(split), 6)

    def test_十三个柜各一件(self):
        codes = [f"0{i}AL" for i in range(1, 14)]
        data = {
            "boxes": [{"code": code} for code in codes],
            "circuits": [],
            "components": [
                {"name": "SPD后备保护器", "spec": "SPD厂家配套", "unit": "只", "quantity": 13,
                 "used_in": "、".join(codes)},
            ],
        }
        split = split_components_by_box(data)
        for code in codes:
            self.assertEqual([row["quantity"] for row in split[code]], [1], code)
        self.assertEqual(component_total(split), 13)

    def test_归不到任何柜的数量不摊给别人_单列未归属并标存疑(self):
        data = {
            "boxes": [{"code": "1AP1"}, {"code": "1AP2"}],
            "circuits": [],
            "components": [
                {"name": "微型断路器", "spec": "MCB-C16A/1P", "unit": "只", "quantity": 2,
                 "used_in": "1AP1 D01、1AP2 D01"},
                {"name": "多功能仪表", "spec": "带通讯接口多功能表", "unit": "只", "quantity": 3,
                 "used_in": "图纸未标柜号"},
            ],
        }
        split = split_components_by_box(data)
        self.assertFalse([r for r in split["1AP1"] if r["name"] == "多功能仪表"])
        self.assertFalse([r for r in split["1AP2"] if r["name"] == "多功能仪表"])
        orphan = _box_rows(split, UNASSIGNED)
        self.assertEqual(len(orphan), 1)
        self.assertEqual(orphan[0]["quantity"], 3)
        self.assertIn("存疑", orphan[0]["note"])
        # 未归属也留在总数里，一件都不许丢
        self.assertEqual(component_total(split), 5)

    def test_片段数与数量不等时按回路逐条明细落到各柜(self):
        """02AL01 8 条回路、02AL02 2 条回路，quantity=10：不能按 8 段比例算成 9/1。"""
        data = {
            "boxes": [{"code": "02AL01"}, {"code": "02AL02"}],
            "circuits": [
                {"box": "02AL01", "circuit_no": f"N{i}", "breaker": "RCBO/2P C20"} for i in range(8)
            ] + [
                {"box": "02AL02", "circuit_no": f"N{i}", "breaker": "RCBO/2P C20"} for i in range(2)
            ],
            "components": [
                # used_in 里同柜的"备用"回路重名，只留下 7 + 1 段，但真实件数是 10
                {"name": "剩余电流动作断路器", "spec": "RCBO/2P C20", "unit": "只", "quantity": 10,
                 "used_in": "02AL01 N0、02AL01 N1、02AL01 N2、02AL01 N3、02AL01 N4、02AL01 N5、"
                            "02AL01 N6、02AL02 N0"},
            ],
        }
        split = split_components_by_box(data)
        self.assertEqual(split["02AL01"][0]["quantity"], 8)
        self.assertEqual(split["02AL02"][0]["quantity"], 2)

    def test_真实任务快照_各柜合计等于项目合计232件(self):
        data = _snapshot()
        split = split_components_by_box(data)
        expected = sum(float(c["quantity"]) for c in data["components"] if c["name"] != "配电箱体")
        self.assertEqual(expected, 232)
        self.assertEqual(component_total(split), 232)
        self.assertFalse(_box_rows(split, UNASSIGNED))
        # 哪一柜拿到的件数都不能超过该规格的全项目数量（旧逻辑把整行给每个命中的柜）
        project_qty = {(c["name"], c["spec"]): int(c["quantity"]) for c in data["components"]}
        for code, rows in split.items():
            for row in rows:
                self.assertLessEqual(row["quantity"], project_qty[(row["name"], row["spec"])], code)
        # 02AP1 的 MCCB 是 2 只（拆量页原来就显示 2，组价不能再算 6）
        mccb = [r for r in split["02AP1"] if r["spec"] == "MCCB-160MA/50A/3P"]
        self.assertEqual([r["quantity"] for r in mccb], [2])

    def test_真实任务快照_组价接口各柜数量与拆分函数一致(self):
        data = _snapshot()
        split = split_components_by_box(data)
        client = TestClient(fastapi_app)
        job_id = _mk_job(data)
        resp = client.get(f"/api/jobs/{job_id}/quotation")
        self.assertEqual(resp.status_code, 200)
        quote = resp.json()
        self.assertTrue(quote["boxes"], "组价结果不能没有箱柜")
        for box in quote["boxes"]:
            code = box["box_code"]
            quoted = {}
            for row in quote["rows"]:
                if row.get("type") != "component" or row.get("box_code") != code:
                    continue
                key = (row["name"], row["design_spec"])
                quoted[key] = quoted.get(key, 0) + int(row["quantity"] or 0)
            self.assertEqual(quoted, _quantities(_box_rows(split, code)), code)
        # 全项目组价元器件件数 = 232，不再是 757
        total_quoted = sum(
            int(row["quantity"] or 0)
            for row in quote["rows"] if row.get("type") == "component"
        )
        self.assertEqual(total_quoted, 232)

    def test_拆量接口与组价同源(self):
        """拆量页每柜元器件直接吃后端拆分结果，与组价页同名同规格同数量。"""
        data = _snapshot()
        client = TestClient(fastapi_app)
        job_id = _mk_job(data)
        detail = client.get(f"/api/jobs/{job_id}").json()
        split = split_components_by_box(data)
        served = {code: _quantities(rows) for code, rows in detail["data"]["box_components"].items() if rows}
        self.assertEqual(served, {code: _quantities(rows) for code, rows in split.items() if rows})
        self.assertEqual(_quantities(detail["data"]["box_components"]["02AP1"]),
                         _quantities(split["02AP1"]))
        # 派生字段只挂在响应上，不写回任务缓存/任务数据
        self.assertNotIn("box_components", jobs[job_id]["data"])


class TestControlCableCircuits(unittest.TestCase):
    def _box(self):
        return [{"code": "02AP1", "size": "GGD", "ip_rating": "IP54"}]

    def test_控制电缆与操作柱回路不生成断路器也不计回路工时(self):
        circuits = [
            {"box": "02AP1", "circuit_no": "D01", "breaker": "MCCB-160MA/50A/3P", "load_name": "1#锅炉给水泵"},
            {"box": "02AP1", "circuit_no": "D01-C1", "cable": "ZR-KYJVP-0.6/1KV-10x1.5-SC32-CT",
             "load_name": "至现场操作柱"},
            {"box": "02AP1", "circuit_no": "D01-C2", "cable": "ZR-KYJVP-0.6/1KV-7x1.5-SC32-CT",
             "load_name": "至设备PLC柜"},
        ]
        q = calculate_box_quotation(self._box()[0], circuits, [], brand="正泰")
        self.assertEqual(len(q["components"]), 1)
        self.assertEqual(q["components"][0]["spec"], "MCCB-160MA/50A/3P")
        # 控制电缆回路没有被补成“分支断路器（规格未标注）”
        self.assertTrue(all(row["spec"] for row in q["components"]))
        # 只有 1 条断路器回路：壳体按 1 回路取档，工时只算 1 条出线
        self.assertEqual(q["circuits_count"], 1)
        self.assertEqual(q["control_circuits_count"], 2)
        self.assertEqual(q["cost_breakdown"]["labor_cost"], 45.0)

    def test_只有控制电缆回路的箱柜不出任何断路器(self):
        circuits = [
            {"box": "02AP1", "circuit_no": "D01-C1", "cable": "ZR-KYJVP-0.6/1KV-10x1.5-SC32-CT",
             "load_name": "至现场操作柱"},
            {"box": "02AP1", "circuit_no": "D01-C2", "cable": "ZR-KYJVP-0.6/1KV-7x1.5-SC32-CT",
             "load_name": "至设备PLC柜"},
        ]
        q = calculate_box_quotation(self._box()[0], circuits, [], brand="正泰")
        self.assertEqual(q["components"], [])
        self.assertEqual(q["circuits_count"], 0)
        self.assertEqual(q["control_circuits_count"], 2)

    def test_控制电缆回路仍留在回路清单里(self):
        data = _snapshot()
        control = [
            c for c in data["circuits"]
            if c.get("box") == "02AP1" and str(c.get("cable") or "").upper().find("KYJVP") >= 0
        ]
        self.assertEqual(len(control), 6)
        split = split_components_by_box(data)
        # 控制电缆回路不产生任何元器件行
        for row in split["02AP1"]:
            self.assertNotIn("至现场操作柱", row["spec"])
            self.assertNotIn("KYJVP", row["spec"])
            self.assertNotIn("分支断路器", row["name"])


if __name__ == "__main__":
    unittest.main()
