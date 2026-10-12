# -*- coding: utf-8 -*-
"""CAD 接口（DXF 下载、CADCraft 元数据、实体搜索、切片渲染）单元测试。"""

import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from app import WORKDIR, app, jobs, save_job


class TestCadEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.job_id = "test_cad_job_01"
        self.sample_dxf = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "../fixtures/ruler/drawings/02-锅炉房-配电系统图一.dxf",
            )
        )
        # 准备任务与工作区 DXF 文件
        self.target_dxf = os.path.join(WORKDIR, f"{self.job_id}.dxf")
        shutil.copyfile(self.sample_dxf, self.target_dxf)

        jobs[self.job_id] = {
            "job_id": self.job_id,
            "status": "done",
            "filename": "02-锅炉房-配电系统图一.dxf",
            "file_type": "dxf",
            "tenant_id": "default",
            "user_id": "admin",
            "progress": 100,
            "pages": 1,
            "circuits": [],
        }
        save_job(self.job_id)

    def tearDown(self):
        jobs.pop(self.job_id, None)
        if os.path.isfile(self.target_dxf):
            os.remove(self.target_dxf)
        # 清理切片缓存
        for f in os.listdir(WORKDIR):
            if f.startswith(f"{self.job_id}_slice_"):
                try:
                    os.remove(os.path.join(WORKDIR, f))
                except OSError:
                    pass

    def test_get_job_dxf(self):
        """测试获取图纸 DXF 下载流。"""
        resp = self.client.get(f"/api/jobs/{self.job_id}/dxf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "application/dxf")
        self.assertGreater(len(resp.content), 1000)

    def test_get_job_cad_info(self):
        """测试调用 CADCraft 获取图纸空间元数据。"""
        resp = self.client.get(f"/api/jobs/{self.job_id}/cad_info")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("available"))
        info = data.get("info", {})
        self.assertIn("entityCount", info)
        self.assertGreater(info["entityCount"], 0)
        self.assertIn("layers", info)

    def test_get_job_cad_find(self):
        """测试在图纸中搜索实体句柄。"""
        resp = self.client.get(f"/api/jobs/{self.job_id}/cad_find?q=锅炉")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("matches", data)
        self.assertGreater(data.get("total", 0), 0)

    def test_get_job_cad_inspect(self):
        """测试根据 Handle 查询实体详细空间坐标与属性。"""
        # 先找到一个匹配的 handle
        find_resp = self.client.get(f"/api/jobs/{self.job_id}/cad_find?q=锅炉")
        self.assertEqual(find_resp.status_code, 200)
        matches = find_resp.json().get("matches", [])
        if matches:
            handle = matches[0]["handle"]
            resp = self.client.get(f"/api/jobs/{self.job_id}/cad_inspect?handle={handle}")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("available"))
            self.assertIn("data", data)
            self.assertEqual(data["data"]["handle"], handle)

    def test_get_job_cad_vectors(self):
        """测试获取纯矢量结构化几何数据流。"""
        resp = self.client.get(f"/api/jobs/{self.job_id}/cad_vectors?max_entities=500")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("available"))
        vectors = data.get("vectors", {})
        self.assertIn("lines", vectors)
        self.assertIn("layers", vectors)
        self.assertIn("extents", vectors)


if __name__ == "__main__":
    unittest.main()

