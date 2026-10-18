# -*- coding: utf-8 -*-
"""系统前置介绍页与登录身份闸门自动化验证套件。

验证内容：
1. 前置介绍页 (Landing Page) 结构与组件齐备性（Hero、拆量双屏对比、组价测算、订阅阶梯、预约表单、统一品牌样式）。
2. 未登录访问身份锁定：工作台被严格隔离隐藏，非登录用户无法直接绕过进入操作台。
3. 身份验证模态框 (Auth Modal) 继承所有工程底纹、滑动切换与无假数据契约。
4. 意向客户预约咨询 (Leads API) 校验闭环：有效联系方式成功登记、非法参数拦截、未授权查询防护。
5. 离线高精度 CAD 示例资产完整性校验。
"""
import os
import pathlib
import unittest
import test_support  # noqa: F401
from fastapi.testclient import TestClient
from app import app
from db import init_db

FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend"


class TestProductIntroAndGateway(unittest.TestCase):
    def setUp(self):
        init_db()
        self.client = TestClient(app)
        self.html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    def test_01_前置介绍页核心结构与锚点齐备(self):
        """前置介绍页必须包含五大核心章节与导航锚点。"""
        # 1. 顶层介绍容器
        self.assertIn('id="authScreen"', self.html)
        self.assertIn('id="introStickyNav"', self.html)

        # 2. 章节锚点
        self.assertIn('id="hero"', self.html)
        self.assertIn('id="takeoff"', self.html)
        self.assertIn('id="pricing-preview"', self.html)
        self.assertIn('id="plans"', self.html)
        self.assertIn('id="lead-form"', self.html)

        # 3. 核心体验入口与模态框挂载
        self.assertIn('id="btnIntroLogin"', self.html)
        self.assertIn('id="btnIntroExperience"', self.html)
        self.assertIn('id="authModal"', self.html)
        self.assertIn('id="btnCloseAuthModal"', self.html)

    def test_02_拆量引擎与工程组价数字指标真实专业(self):
        """验证介绍页中的专业展示指标（2SAL3图纸解析、回路拆量、铜价联动、正泰/德力西/施耐德品牌）。"""
        # 拆量引擎
        self.assertIn("智能图纸拆量", self.html)
        self.assertIn("2SAL3", self.html)
        self.assertIn("DS-50/3P", self.html)
        self.assertIn("stitch_blueprint_2sal3.png", self.html)

        # 组价测算
        self.assertIn("智能工程组价", self.html)
        self.assertIn("152,300", self.html)
        self.assertIn("38,400", self.html)
        self.assertIn("30,400", self.html)
        self.assertIn("72,000", self.html)
        self.assertIn("269,830", self.html)
        self.assertIn("CHINT 正泰", self.html)
        self.assertIn("德力西电气", self.html)
        self.assertIn("施耐德 Schneider", self.html)

        # 阶梯方案
        self.assertIn("免费体验版", self.html)
        self.assertIn("专业版", self.html)
        self.assertIn("企业高级版", self.html)
        self.assertIn("BYOK", self.html)

    def test_03_设计规范与离线资产完整(self):
        """验证所有引用的字体、样式规范及本地 CAD 蓝图资产。"""
        # 字体与设计语言
        self.assertIn("JetBrains Mono", self.html)
        self.assertIn("Geist", self.html)

        # 本地离线 CAD 资产
        cad_img = FRONTEND_DIR / "assets" / "stitch_blueprint_2sal3.png"
        self.assertTrue(cad_img.exists(), "CAD 离线示例切片图片不存在")
        self.assertGreater(cad_img.stat().st_size, 10240, "CAD 图片尺寸异常，必须是真实图纸")
        with open(cad_img, "rb") as f:
            header = f.read(8)
            self.assertEqual(header, b"\x89PNG\r\n\x1a\n", "CAD 切片必须为有效 PNG 格式")

        # 品牌 Logo 资产
        logo_img = FRONTEND_DIR / "assets" / "brand" / "02_品牌Logo" / "17_App图标主体.png"
        self.assertTrue(logo_img.exists(), "品牌图标不存在")

    def test_04_前端路由与身份锁定闸门机制(self):
        """未登录时工作台元素强制隐藏，路由跳转被拦截并弹出登录。"""
        # 页面初始锁定状态
        self.assertIn("auth-locked", self.html)
        self.assertIn("body.auth-locked #globalHeader", self.html)
        self.assertIn("body.auth-locked main.view", self.html)

        # 路由拦截
        self.assertIn("if (!Auth.user) { Auth.show('login'); return; }", self.js)

        # 介绍页重新回访入口（工作台顶部与下拉菜单）
        self.assertIn('id="btnOpenIntro"', self.html)
        self.assertIn('id="btnDropdownIntro"', self.html)

    def test_05_客户预约咨询接口业务闭环(self):
        """测试 POST /api/leads 正常登记与非法参数校验。"""
        # 1. 成功提交有效咨询
        res = self.client.post("/api/leads", json={
            "name": "陈总工",
            "phone": "13912345678",
            "company": "华东成套电气集团",
            "source": "landing_priority_access"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["lead"]["name"], "陈总工")
        self.assertEqual(data["lead"]["phone"], "13912345678")
        self.assertEqual(data["lead"]["company"], "华东成套电气集团")
        lead_id = data["lead"]["id"]

        # 2. 校验异常手机号（少于7位）
        err_res = self.client.post("/api/leads", json={
            "name": "李工",
            "phone": "123",
            "company": "测试公司"
        })
        self.assertEqual(err_res.status_code, 400)
        self.assertIn("请提供有效的联系电话", err_res.json()["detail"])

        # 3. 校验姓名缺失
        err_res2 = self.client.post("/api/leads", json={
            "name": "",
            "phone": "13800000000"
        })
        self.assertEqual(err_res2.status_code, 400)
        self.assertIn("请提供姓名", err_res2.json()["detail"])

        # 4. 未授权无法读取咨询列表
        unauth_res = self.client.get("/api/leads")
        self.assertEqual(unauth_res.status_code, 401)

        # 5. 管理员登录后可查看客户咨询列表
        login_res = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login_res.json()["token"]
        list_res = self.client.get("/api/leads", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(list_res.status_code, 200)
        leads = list_res.json().get("leads", [])
        self.assertTrue(any(l["id"] == lead_id for l in leads))

    def test_06_注册登录流程验证身份放行工作台(self):
        """验证新用户注册后获取合法 Token 并成功放行。"""
        import secrets
        uname = f"contractor_{secrets.token_hex(4)}"
        reg_res = self.client.post("/api/auth/register", json={
            "username": uname,
            "password": "Password123!",
            "display_name": "张工程总监",
            "tenant_name": "南京精益开关设备厂"
        })
        self.assertEqual(reg_res.status_code, 200)
        token = reg_res.json()["token"]
        self.assertTrue(token.startswith("tk_"))

        # 验证能顺利读取已授权的工程概览
        me_res = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_res.status_code, 200)
        self.assertTrue(me_res.json()["authenticated"])
        self.assertEqual(me_res.json()["user"]["username"], uname)


if __name__ == "__main__":
    unittest.main()
