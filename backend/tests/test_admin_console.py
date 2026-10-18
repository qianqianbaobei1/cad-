# -*- coding: utf-8 -*-
"""后台管理中枢、账号冻结权益管控与全量数据导出测试 (Admin Console & Governance Test Suite)。"""
import io
import secrets
import unittest
import openpyxl
from fastapi.testclient import TestClient

import test_support  # noqa: F401
from app import app
from db import init_db, db_get_user_by_token, db_list_leads


class AdminConsoleTest(unittest.TestCase):
    def setUp(self):
        init_db()
        import app as app_module
        app_module._LEAD_RATE.clear()
        self.client = TestClient(app)
        res = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(res.status_code, 200, res.text)
        self.admin_token = res.json()["token"]
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}

    def _register_user(self, **extra):
        username = f"user_{secrets.token_hex(4)}"
        payload = {
            "username": username,
            "password": "pwd_123456",
            "display_name": "测试工程师",
            "tenant_name": "华东配电测试厂",
            "contact": "13912345678",
        }
        payload.update(extra)
        res = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        return username, data["user"]["id"], data["token"]

    def test_账号冻结立即下线且禁止登录(self):
        """测试：冻结账号后原 Token 立即作废，再次登录报 403 明确提示冻结。"""
        username, user_id, user_token = self._register_user()

        # 1. 验证正常状态下能成功调用鉴权接口
        me_res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"})
        self.assertEqual(me_res.status_code, 200)
        self.assertTrue(me_res.json()["authenticated"])
        self.assertEqual(me_res.json()["user"]["username"], username)

        # 2. 管理员执行冻结操作
        freeze_res = self.client.post(
            f"/api/admin/users/{user_id}/status",
            json={"status": "frozen"},
            headers=self.admin_headers,
        )
        self.assertEqual(freeze_res.status_code, 200, freeze_res.text)
        self.assertEqual(freeze_res.json()["result"]["status"], "frozen")

        # 3. 验证原 Token 瞬间作废
        me_frozen = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"})
        self.assertEqual(me_frozen.status_code, 200)
        self.assertFalse(me_frozen.json()["authenticated"], "被冻结用户的 Token 必须立即失效")

        # 4. 验证再次登录被拦截（HTTP 403）
        login_res = self.client.post("/api/auth/login", json={"username": username, "password": "pwd_123456"})
        self.assertEqual(login_res.status_code, 403, "被冻结账号登录必须报 403 拦截")
        self.assertIn("冻结", login_res.json()["detail"])

        # 5. 管理员执行解冻恢复
        unfreeze_res = self.client.post(
            f"/api/admin/users/{user_id}/status",
            json={"status": "active"},
            headers=self.admin_headers,
        )
        self.assertEqual(unfreeze_res.status_code, 200)
        self.assertEqual(unfreeze_res.json()["result"]["status"], "active")

        # 6. 解冻后可以正常重新登录
        login_ok = self.client.post("/api/auth/login", json={"username": username, "password": "pwd_123456"})
        self.assertEqual(login_ok.status_code, 200)
        self.assertTrue(login_ok.json()["ok"])
        self.assertTrue(login_ok.json()["token"].startswith("tk_"))

    def test_禁止冻结根管理员自身(self):
        """测试：平台保护策略，严禁冻结平台根管理员或登录自身。"""
        # 冻结自身
        admin_info = self.client.get("/api/auth/me", headers=self.admin_headers).json()["user"]
        res = self.client.post(
            f"/api/admin/users/{admin_info['id']}/status",
            json={"status": "frozen"},
            headers=self.admin_headers,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("禁止冻结", res.json()["detail"])

        # 冻结 admin_default
        res2 = self.client.post(
            "/api/admin/users/admin_default/status",
            json={"status": "frozen"},
            headers=self.admin_headers,
        )
        self.assertEqual(res2.status_code, 400)

    def test_管理员重置用户密码(self):
        """测试：管理员重置用户密码，原密码失效，新密码生效且作废原 Token。"""
        username, user_id, user_token = self._register_user()

        # 重置密码
        new_pwd = "new_secret_pwd_888"
        res = self.client.post(
            f"/api/admin/users/{user_id}/reset_password",
            json={"password": new_pwd},
            headers=self.admin_headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["ok"])

        # 原 Token 失效
        me_res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"})
        self.assertFalse(me_res.json()["authenticated"])

        # 旧密码登录失败
        fail_login = self.client.post("/api/auth/login", json={"username": username, "password": "pwd_123456"})
        self.assertEqual(fail_login.status_code, 401)

        # 新密码登录成功
        ok_login = self.client.post("/api/auth/login", json={"username": username, "password": new_pwd})
        self.assertEqual(ok_login.status_code, 200)
        self.assertTrue(ok_login.json()["ok"])

    def test_客户预约线索与跟进备注全流程(self):
        """测试：提交含需求留言的线索、管理员查看线索、更新跟进状态与备注。"""
        phone = f"139{secrets.randbelow(90000000) + 10000000}"
        content_text = "需要支持 300 台配电箱系统图一键导出正泰元器件报价清单"
        
        # 1. 前台提交预约
        lead_res = self.client.post("/api/leads", json={
            "phone": phone,
            "name": "张工",
            "company": "上海电气成套工程部",
            "content": content_text,
            "source": "landing_test",
        })
        self.assertEqual(lead_res.status_code, 200, lead_res.text)
        lead_id = lead_res.json()["lead"]["id"]
        self.assertEqual(lead_res.json()["lead"]["content"], content_text)

        # 2. 管理员查看线索列表
        list_res = self.client.get("/api/admin/leads", headers=self.admin_headers)
        self.assertEqual(list_res.status_code, 200)
        leads = list_res.json()["leads"]
        target = next((l for l in leads if l["id"] == lead_id), None)
        self.assertIsNotNone(target)
        self.assertEqual(target["name"], "张工")
        self.assertEqual(target["content"], content_text)
        self.assertEqual(target["status"], "pending")

        # 3. 管理员更新跟进状态与备注
        notes_text = "已电话沟通，客户对正泰昆仑平替功能非常感兴趣，预约下周三线上面试内测。"
        update_res = self.client.post(
            f"/api/admin/leads/{lead_id}/update",
            json={"status": "contacted", "notes": notes_text},
            headers=self.admin_headers,
        )
        self.assertEqual(update_res.status_code, 200, update_res.text)
        updated = update_res.json()["lead"]
        self.assertEqual(updated["status"], "contacted")
        self.assertEqual(updated["notes"], notes_text)

        # 4. 再次查询验证落盘持久化
        all_leads = db_list_leads()
        db_target = next((l for l in all_leads if l["id"] == lead_id), None)
        self.assertIsNotNone(db_target)
        self.assertEqual(db_target["status"], "contacted")
        self.assertEqual(db_target["notes"], notes_text)

    def test_全量数据导出多Sheet_Excel(self):
        """测试：导出多 Sheet 咨询级商业 Excel 文件，校验结构与数据。"""
        # 产生一些数据
        self._register_user()
        self.client.post("/api/leads", json={
            "phone": "13811223344",
            "name": "刘总",
            "company": "北京电力开关设备厂",
            "content": "咨询集团定制化部署方案",
        })

        # 请求导出接口
        res = self.client.get("/api/admin/export", headers=self.admin_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("attachment; filename=", res.headers.get("content-disposition", ""))

        # 解析 Excel
        excel_bytes = io.BytesIO(res.content)
        wb = openpyxl.load_workbook(excel_bytes)
        sheet_names = wb.sheetnames

        self.assertIn("客户预约与咨询台账", sheet_names)
        self.assertIn("租户企业与账号权益", sheet_names)
        self.assertIn("AI模型与BYOK密钥配置", sheet_names)

        # 检查表头
        ws_leads = wb["客户预约与咨询台账"]
        headers_leads = [cell.value for cell in ws_leads[1]]
        self.assertIn("客户姓名", headers_leads)
        self.assertIn("联系电话", headers_leads)
        self.assertIn("需求内容与留言", headers_leads)
        self.assertIn("商务跟进备注", headers_leads)

        ws_users = wb["租户企业与账号权益"]
        headers_users = [cell.value for cell in ws_users[1]]
        self.assertIn("登录账号", headers_users)
        self.assertIn("账号状态", headers_users)
        self.assertIn("所属企业名称", headers_users)

        ws_keys = wb["AI模型与BYOK密钥配置"]
        headers_keys = [cell.value for cell in ws_keys[1]]
        self.assertIn("视觉服务商", headers_keys)
        self.assertIn("Key配置状态", headers_keys)
        self.assertIn("API Key (安全脱敏)", headers_keys)

    def test_普通用户或未认证访问被拒绝(self):
        """测试：越权访问拦截，非管理员访问 /api/admin/* 均被 401/403 拦截。"""
        # 1. 未带 Token 访问
        unauth_res = self.client.get("/api/admin/leads")
        self.assertIn(unauth_res.status_code, (401, 403))

        # 2. 普通企业租户管理员访问平台全量线索与全局配置
        _, _, normal_token = self._register_user()
        normal_headers = {"Authorization": f"Bearer {normal_token}"}

        # 普通企业租户访问平台线索报 403
        leads_res = self.client.get("/api/admin/leads", headers=normal_headers)
        self.assertEqual(leads_res.status_code, 403)

        # 普通租户尝试冻结账号报 403
        freeze_res = self.client.post("/api/admin/users/dummy/status", json={"status": "frozen"}, headers=normal_headers)
        self.assertEqual(freeze_res.status_code, 403)

        # 普通租户尝试全量导出报 403
        export_res = self.client.get("/api/admin/export", headers=normal_headers)
        self.assertEqual(export_res.status_code, 403)

    def test_管理页面路由正常返回(self):
        """测试：/admin, /admin/console, /admin/console.html 页面均返回 HTTP 200 HTML。"""
        for path in ("/admin", "/admin/console", "/admin/console.html"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"Path {path} 应当正常返回 200")
            self.assertIn("电柜智核 · 全域运维与商务管理中枢", res.text)


if __name__ == "__main__":
    unittest.main()
