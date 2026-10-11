# -*- coding: utf-8 -*-
"""一次系统表只在表格线够密时改道。单张系统图仍走原来的整页读取。"""
import unittest
from pathlib import Path

from extractor.schedule import assign_columns, detect_schedules

DRAWINGS = Path(__file__).resolve().parents[1] / "fixtures" / "ruler" / "drawings"


class ScheduleDetectionTests(unittest.TestCase):
    def test_single_line_diagrams_are_not_schedules(self):
        for name in ("2SAL3.pdf", "2SAL2.pdf", "2ALE.pdf", "5DT-AT.pdf", "2AT.pdf"):
            self.assertEqual(detect_schedules(str(DRAWINGS / name)), [])

    def test_boiler_tables_split_into_the_eight_cabinets(self):
        tables = detect_schedules(str(DRAWINGS / "02-锅炉房-8台柜子.pdf"))
        self.assertEqual(len(tables), 2)
        self.assertEqual([len(group) for group in tables[0].groups(0)], [1, 1, 2, 12, 1])
        self.assertEqual([len(group) for group in tables[1].groups(0)], [1, 1, 2, 2, 1])

    def test_merged_cells_share_one_value_and_the_model_row_is_not_the_name(self):
        table = detect_schedules(str(DRAWINGS / "02-锅炉房-8台柜子.pdf"))[0]
        rows = len(table.rows)
        columns = [[""] * rows for _ in table.columns]
        columns[0] = [
            "低压配电柜编号", "低压配电柜型号", "空气开关", "交流接触器", "电流互感器",
            "热继电器", "设备容量 kW", "回路编号", "导线型号及规格", "计算电流 A",
            "回路名称", "二次图编号", "启动方式",
        ]
        columns[1][0] = "C01"
        columns[1][1] = "GGD"
        columns[1][6] = "281kW(最大同时使用容量:195kW)"
        columns[1][8] = "进电"
        columns[2][0] = "C02"
        columns[2][2] = "MCCB-250MA/200A/3P"
        columns[2][7] = "S-1a-P"
        columns[3][2] = "MCCB-250MA/200A/3P"
        columns[3][7] = "S-1b-P"
        columns[3][10] = "喷淋泵"
        boxes, circuits = assign_columns(table, columns)
        self.assertEqual([box["code"] for box in boxes], ["C01", "C02"])
        self.assertTrue(all(box["name"] == "" for box in boxes))
        by_number = {row["circuit_no"]: row for row in circuits}
        self.assertEqual(by_number[""]["cable"], "进电")
        self.assertEqual(by_number[""]["box"], "C01")
        self.assertEqual(by_number["S-1a-P"]["load_name"], "喷淋泵")
        self.assertEqual(by_number["S-1b-P"]["load_name"], "喷淋泵")
        self.assertEqual(by_number["S-1a-P"]["box"], "C02")

    def test_a_double_height_cable_cell_does_not_push_the_load_name_down(self):
        table = detect_schedules(str(DRAWINGS / "02-锅炉房-8台柜子.pdf"))[0]
        rows = len(table.rows)
        columns = [[""] * rows for _ in table.columns]
        columns[0] = [
            "低压配电柜编号", "低压配电柜型号", "空气开关", "交流接触器", "电流互感器",
            "热继电器", "设备容量 kW", "回路编号", "导线型号及规格", "",
            "计算电流 A", "回路名称", "二次图编号",
        ]
        columns[2][0] = "C02"
        columns[2][2] = "MCCB-250MA/200A/3P"
        columns[2][7] = "S-1a-P"
        columns[2][8] = "ZRNH-YJV-0.6/1kV-4×70-SC80"
        columns[2][9] = "ZRNH-YJV-0.6/1kV-3×70-SC80"
        columns[2][11] = "喷淋泵"
        columns[2][12] = "详设备成套控制原理图(两用一备,互为备用)"
        _boxes, circuits = assign_columns(table, columns)
        row = next(item for item in circuits if item["circuit_no"] == "S-1a-P")
        self.assertEqual(row["load_name"], "喷淋泵")
        self.assertIn("4×70", row["cable"])
        self.assertIn("3×70", row["cable"])
        self.assertNotIn("167", row["cable"])
        columns[5][0] = "C03"
        columns[5][7] = "XKP-1-P"
        columns[5][8] = "ZRNH-YJV-0.6/1kV-5×6-SC32"
        columns[5][9] = "167"
        columns[5][11] = "潜污泵控制箱"
        _boxes, circuits = assign_columns(table, columns)
        other = next(item for item in circuits if item["circuit_no"] == "XKP-1-P")
        self.assertEqual(other["cable"], "ZRNH-YJV-0.6/1kV-5×6-SC32")
        self.assertEqual(other["load_name"], "潜污泵控制箱")
