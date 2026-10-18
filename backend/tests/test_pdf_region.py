# -*- coding: utf-8 -*-
"""T9：PDF 框选区域提表（模块 + 接口）。"""
import os
import secrets
import unittest

import test_support  # noqa: F401  隔离测试数据目录

import pymupdf
from fastapi.testclient import TestClient

import app as app_module
from extractor.pdf_region import RegionError, extract_region


def _make_table_pdf(path: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    for x in (40, 200, 400, 560):
        page.draw_line((x, 40), (x, 220))
    for y in (40, 85, 130, 175, 220):
        page.draw_line((40, y), (560, y))
    page.insert_text((55, 70), "箱号", fontsize=12)
    page.insert_text((215, 70), "回路", fontsize=12)
    page.insert_text((415, 70), "型号", fontsize=12)
    page.insert_text((55, 115), "02AL01", fontsize=12)
    page.insert_text((215, 115), "N1", fontsize=12)
    page.insert_text((415, 115), "C9 2P C20A", fontsize=12)
    doc.save(path)
    doc.close()


class PdfRegionModuleTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(app_module.WORKDIR, "pdf_region_test")
        os.makedirs(self.dir, exist_ok=True)
        self.pdf = os.path.join(self.dir, "table.pdf")
        _make_table_pdf(self.pdf)

    def tearDown(self):
        try:
            os.remove(self.pdf)
        except OSError:
            pass

    def test_框选区域返回行列与来源(self):
        out = extract_region(self.pdf, 1, {"x0": 0.05, "y0": 0.08, "x1": 0.95, "y1": 0.57})
        self.assertTrue(out["ok"])
        texts = [c["text"] for row in out["rows"] for c in row]
        self.assertIn("02AL01", texts)
        self.assertTrue(any("C9 2P C20A" in t for t in texts))
        self.assertIn(out["source"], {"PDF_TABLE", "PDF_TEXT", "OCR"})
        for row in out["rows"]:
            for cell in row:
                self.assertIn(cell["source"], {"PDF_TABLE", "PDF_TEXT", "OCR"})

    def test_非法矩形与页码被拦截(self):
        with self.assertRaises(RegionError):
            extract_region(self.pdf, 1, {"x0": 0.5, "y0": 0.5, "x1": 0.5, "y1": 0.6})
        with self.assertRaises(RegionError):
            extract_region(self.pdf, 99, {"x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.9})
        with self.assertRaises(RegionError):
            extract_region(self.pdf, 1, {"x0": -0.1, "y0": 0.1, "x1": 0.9, "y1": 0.9})


class PdfRegionEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)
        self.job_id = f"pdfregion{secrets.token_hex(3)}"
        self.pdf = os.path.join(app_module.WORKDIR, f"{self.job_id}.pdf")
        app_module.jobs[self.job_id] = {
            "job_id": self.job_id,
            "tenant_id": "default",
            "filename": "test.pdf",
            "status": "done",
        }

    def tearDown(self):
        app_module.jobs.pop(self.job_id, None)
        try:
            os.remove(self.pdf)
        except OSError:
            pass

    def test_没有PDF返回400(self):
        res = self.client.post(f"/api/jobs/{self.job_id}/pdf_region",
                               json={"page": 1, "rect": {"x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.9}})
        self.assertEqual(res.status_code, 400)

    def test_接口返回表格(self):
        _make_table_pdf(self.pdf)
        res = self.client.post(f"/api/jobs/{self.job_id}/pdf_region",
                               json={"page": 1, "rect": {"x0": 0.05, "y0": 0.08, "x1": 0.95, "y1": 0.57}})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertTrue(any("02AL01" in c["text"] for row in data["rows"] for c in row))

    def test_非法矩形返回400(self):
        _make_table_pdf(self.pdf)
        res = self.client.post(f"/api/jobs/{self.job_id}/pdf_region",
                               json={"page": 1, "rect": {"x0": 0.5, "y0": 0.5, "x1": 0.5, "y1": 0.6}})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
