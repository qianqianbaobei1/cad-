# -*- coding: utf-8 -*-
"""前端登录闸门的静态契约：没登录就不许进工作台。

这里断言的是"闸门还在"，不是界面像素——真机交互另有 CDP 手工验收。
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend"


class FrontendAuthGateTest(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.js = (ROOT / "app.js").read_text(encoding="utf-8")

    def test_首屏默认锁定且登录注册都在(self):
        self.assertIn('class="bg-surface font-body-md text-on-surface h-screen overflow-hidden flex flex-col auth-locked"', self.html)
        for el in ('id="authScreen"', 'id="loginForm"', 'id="registerForm"',
                   'id="loginUsername"', 'id="regTenant"', 'id="regPassword2"'):
            self.assertIn(el, self.html)

    def test_锁定态隐藏工作台(self):
        self.assertIn("body.auth-locked #globalHeader", self.html)
        self.assertIn("body.auth-locked main.view", self.html)
        self.assertIn("body.auth-locked #authScreen { display: block; }", self.html)

    def test_路由有登录闸门(self):
        self.assertIn("if (!Auth.user) { Auth.show('login'); return; }", self.js)

    def test_令牌失效自动退回登录(self):
        self.assertIn("Auth.onUnauthorized()", self.js)
        self.assertIn("localStorage.removeItem('dgzh_auth_token')", self.js)
        self.assertIn("登录状态已失效，请重新登录", self.js)

    def test_退出登录走统一出口(self):
        self.assertIn("$('btnLogout').addEventListener('click', () => Auth.signOut(true));", self.js)


if __name__ == "__main__":
    unittest.main()
