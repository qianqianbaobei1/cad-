# -*- coding: utf-8 -*-
"""上传边界：超过 MAX_UPLOAD_MB 必须 413，且不留下半截文件。"""
import os
import unittest
from unittest.mock import patch

import test_support  # noqa: F401

from fastapi.testclient import TestClient
import app as app_module


class UploadLimitTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_上传超过上限返回413(self):
        before = set(os.listdir(app_module.WORKDIR))
        with patch.dict(os.environ, {"MAX_UPLOAD_MB": "0"}, clear=False):
            res = self.client.post(
                "/api/jobs",
                files={"file": ("demo.pdf", b"%PDF-1.4 tiny", "application/pdf")},
            )
        self.assertEqual(res.status_code, 413)
        new_files = set(os.listdir(app_module.WORKDIR)) - before
        self.assertEqual(new_files, set(), "超限上传不得留下半截文件")


if __name__ == "__main__":
    unittest.main()
