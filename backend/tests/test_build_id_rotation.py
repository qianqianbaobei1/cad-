# -*- coding: utf-8 -*-
"""T16：构建号变化必须作废全平台旧令牌，同版本重启保留会话。"""
import secrets
import unittest

import test_support  # noqa: F401  隔离测试数据目录

import app as app_module
import db
from fastapi.testclient import TestClient


class BuildIdRotationTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.original_build = app_module.BUILD_ID
        app_module.BUILD_ID = f"test-{secrets.token_hex(4)}"
        db.db_set_meta(app_module.BUILD_ID_META_KEY, "old-build")

    def tearDown(self):
        app_module.BUILD_ID = self.original_build
        db.db_set_meta(app_module.BUILD_ID_META_KEY, self.original_build)

    def _new_user(self) -> dict:
        username = f"rotation_{secrets.token_hex(4)}"
        return db.db_register_user(username, "Password123!", "旋转测试", "旋转测试厂")

    def test_版本变化清空全部旧令牌(self):
        user = self._new_user()
        self.assertIsNotNone(db.db_get_user_by_token(user["token"]))
        cleared = app_module._apply_build_rotation()
        self.assertGreaterEqual(cleared, 1)
        self.assertIsNone(db.db_get_user_by_token(user["token"]))

    def test_同版本重启保留令牌(self):
        user = self._new_user()
        db.db_set_meta(app_module.BUILD_ID_META_KEY, app_module.BUILD_ID)
        self.assertEqual(app_module._apply_build_rotation(), 0)
        self.assertIsNotNone(db.db_get_user_by_token(user["token"]))

    def test_首页注入构建号且不残留占位符(self):
        client = TestClient(app_module.app)
        res = client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("__BUILD_ID__", res.text)
        self.assertIn(f"app.js?v={app_module.BUILD_ID}", res.text)

    def test_健康检查返回构建号(self):
        client = TestClient(app_module.app)
        res = client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["build_id"], app_module.BUILD_ID)


if __name__ == "__main__":
    unittest.main()
