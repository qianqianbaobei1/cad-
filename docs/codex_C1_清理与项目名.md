# Codex 任务 C1：清理死代码 + Dockerfile 版本统一 + 项目名空括号（T10 / T6 / T14）

仓库：/Users/qianqianawodebaobei/PycharmProjects/codex/工作区项目/图纸扒图AI应用_drawing-extractor（分支 fix/catalog-plan-ranges）
并行说明：另一个代理（pi）同时在 .worktrees/pi 里改 backend/extractor/excel.py、box_components.py、assemble.py、pricing.py、frontend/app.js。**这些文件本任务一律不要改**；不要动 .worktrees/、.venv、backend/fonts/（含授权字体，已在 .git/info/exclude，绝不能提交）。
完成后：跑全量测试 `cd backend && ../.venv/bin/python -m pytest -q`（基线 424 passed，不得新增失败），**提交到 fix/catalog-plan-ranges**（不要切分支、不要 force push、不要改写历史），提交信息写清改了什么。

## 1. 清理（T10）
- 删除 backend/audit_and_verify_pipeline.py（引用已删除的 extract_cad_table_data，跑不起来）、backend/stability_test.py；先 grep 确认没有别处引用（含 Dockerfile、README、scripts、tests）。
- backend/extractor/native_takeoff.py 第 895 行用了 `Iterable` 但没导入：在 `from typing import Any` 处补上 `Iterable`（只改这一行导入，不动别的）。
- backend/config/pipeline.json 里已不被任何代码读取的旧切图配置：逐个 key grep，确认无引用后删除；有引用的保留。汇报列出删了哪些 key。
- **frontend/legacy/ 保留，不要删**（用户之前要求旧版保留在 legacy/）。
- 删除仓库根目录/ docs 下明显的过程性临时文件前先列清单给用户确认，本任务不直接删文档。

## 2. Dockerfile 与 cad_bootstrap 的 LibreDWG 版本统一（T6）
- Dockerfile 第 9-12 行编译 libredwg-0.12.5；backend/cad_bootstrap.py 第 156-163 行下载 libredwg-0.14（带 sha256 校验）。统一为 0.14：Dockerfile 改用 0.14 的 tarball 和 cad_bootstrap.py 里同一个 sha256。构建步骤若因版本变化需要调整（configure 参数等）一并处理。
- 能本地 `docker build` 就验证一次；不能就在汇报里说明未验证。

## 3. 项目名残留空括号（T14）
- 现象：文件名“02-锅炉房-电气921(电子签名版)_t3_t3.dwg”得到的项目名是“锅炉房-电气921()”。括号里的“电子签名版”被清掉了，括号留下。
- 位置：项目名推导在 backend/app.py 约 440-495 行（返回 {"project": ..., "name_source": ...} 的函数及其清洗逻辑），以及 backend/extractor/excel.py 的 `_clean_proj_title`（excel.py 本任务不改，如果根因在那里，请在汇报里说明，交给 pi）。
- 修法：清洗后去掉空括号（半角/全角，()、（）、[]、【】），并收拾多余的连接符/空格；加单元测试覆盖：“(电子签名版)”“（出图）”“_t3_t3”后缀、正常带括号的有效名（如“1#楼（东区）”应保留）。

## 汇报
1 改动清单 2 删除的文件和配置 key 3 Docker 是否实测 4 pytest 数字 5 提交号 6 需要用户决定的事
