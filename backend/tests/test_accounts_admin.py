# -*- coding: utf-8 -*-
"""注册信息要能落到后台、并且只有管理员看得到。

产品要求：别人注册完，企业、姓名、账号、联系方式必须能从后台查出来；
普通账号看不到别家企业的账号列表。
"""
import secrets
import unittest

import test_support  # noqa: F401
from fastapi.testclient import TestClient

from app import app
from db import init_db


class AccountsAdminTest(unittest.TestCase):
    def setUp(self):
        init_db()
        self.client = TestClient(app)
        res = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(res.status_code, 200)
        self.admin_token = res.json()["token"]

    def _register(self, **extra):
        username = f"reg_{secrets.token_hex(4)}"
        payload = {
            "username": username,
            "password": "abc12345",
            "display_name": "李工",
            "tenant_name": "验收注册电气有限公司",
            "contact": "13800000000",
        }
        payload.update(extra)
        res = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(res.status_code, 200, res.text)
        return username, res.json()

    def test_注册信息能从后台查出来(self):
        username, reg = self._register()
        res = self.client.get("/api/admin/accounts",
                              headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        row = next((a for a in data["accounts"] if a["username"] == username), None)
        self.assertIsNotNone(row, "注册的账号必须出现在后台账号列表里")
        self.assertEqual(row["tenant_name"], "验收注册电气有限公司")
        self.assertEqual(row["display_name"], "李工")
        self.assertEqual(row["contact"], "13800000000")
        self.assertEqual(row["role"], "admin")
        self.assertTrue(row["created_at"])
        self.assertEqual(reg["user"]["tenant_name"], "验收注册电气有限公司")

    def test_新注册账号最近登录时间会被记录(self):
        username, reg = self._register()
        res = self.client.get("/api/admin/accounts",
                              headers={"Authorization": f"Bearer {self.admin_token}"})
        row = next(a for a in res.json()["accounts"] if a["username"] == username)
        self.assertTrue(row["last_login_at"], "注册即登录，最近登录时间应有值")

    def test_普通企业管理员只看得到自己家账号(self):
        """自己注册的账号也是本企业管理员，但绝不能读到别家企业的账号。"""
        username, reg = self._register()
        six_token = reg["token"]
        res = self.client.get("/api/admin/accounts", headers={"Authorization": f"Bearer {six_token}"})
        self.assertEqual(res.status_code, 200)
        rows = res.json()["accounts"]
        self.assertTrue(rows)
        self.assertTrue(all(r["tenant_id"] == reg["user"]["tenant_id"] for r in rows),
                        "非平台管理员不能看到别家企业")
        self.assertEqual([r["username"] for r in rows], [username])

    def test_未登录看不了账号列表(self):
        res = self.client.get("/api/admin/accounts")
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
