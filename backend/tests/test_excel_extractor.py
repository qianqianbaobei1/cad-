# -*- coding: utf-8 -*-
"""测试设备清单直接组价与标准模板下载接口。"""
import os
from fastapi.testclient import TestClient

import test_support  # noqa: F401
from app import app
from extractor.excel_extractor import create_standard_bom_template, parse_equipment_bom_file

client = TestClient(app)


def test_create_and_parse_standard_bom_template(tmp_path):
    tpl_path = str(tmp_path / "test_template.xlsx")
    create_standard_bom_template(tpl_path)
    assert os.path.exists(tpl_path)
    assert os.path.getsize(tpl_path) > 1000

    raw = parse_equipment_bom_file(tpl_path)
    assert len(raw.boxes) >= 3
    assert len(raw.extra_devices) >= 10

    box_codes = [b.code for b in raw.boxes]
    assert "1AL1" in box_codes
    assert "1AP1" in box_codes


def test_bom_template_api_endpoint():
    res = client.get("/api/template/bom")
    assert res.status_code == 200
    assert "application/vnd.openxmlformats" in res.headers["content-type"]
    assert len(res.content) > 1000


def test_upload_excel_bom_pipeline(tmp_path):
    tpl_path = str(tmp_path / "equipment_list.xlsx")
    create_standard_bom_template(tpl_path)

    with open(tpl_path, "rb") as f:
        res = client.post("/api/jobs", files={"file": ("equipment_list.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

    assert res.status_code == 200
    job_id = res.json()["job_id"]
    assert job_id

    # 查询详情
    res_job = client.get(f"/api/jobs/{job_id}")
    assert res_job.status_code == 200
    job_data = res_job.json()
    assert job_data["status"] == "done"
    assert job_data["file_type"] == "xlsx"
    assert len(job_data["data"]["boxes"]) >= 3

    # 查询列表
    res_list = client.get("/api/jobs")
    assert res_list.status_code == 200
    jobs = res_list.json()["jobs"]
    target = next((j for j in jobs if j["job_id"] == job_id), None)
    assert target is not None
    assert target["file_type"] == "xlsx"
    assert target["total_price"] is not None and target["total_price"] > 0
    assert target["boxes_count"] >= 3

    # 删除任务清理
    res_del = client.delete(f"/api/jobs/{job_id}")
    assert res_del.status_code == 200
    assert res_del.json()["ok"] is True
