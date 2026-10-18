# Codex 任务 C2：识别准确率（T4 / T5），只改识别引擎 takeoff.py

仓库：/Users/qianqianawodebaobei/PycharmProjects/codex/工作区项目/图纸扒图AI应用_drawing-extractor（分支 fix/catalog-plan-ranges）
并行说明：另一个代理（pi）同时在改 backend/extractor/excel.py、box_components.py、assemble.py、pricing.py、backend/app.py、frontend/app.js。**本任务只改 backend/extractor/takeoff.py（及它私有的辅助模块，如 sheets.py）和 backend/tests/ 下新增测试**；其它文件要改先在汇报里提出，不要动。不要动 .worktrees/、.venv、backend/fonts/。
完成后跑全量测试（`cd backend && ../.venv/bin/python -m pytest -q`，基线 424 passed，不得新增失败），**提交到 fix/catalog-plan-ranges**（不切分支、不 force push）。建议在 C1 提交之后再开始，或者两者改不同文件、各自提交。

## 基准（真值来自客户正式报价单，只读，不要复制进仓库、不要提交）
目录：/private/tmp/claude-501/-Users-qianqianawodebaobei-PycharmProjects/1cc367c3-de46-4984-88b5-a7f57f3ae639/scratchpad/bench/
- truth.json：客户报价单的 148 台柜（柜号 → 各元器件/断路器数量等）
- 5 张项目 DWG：00-总图、01-制丝工房-动力配电0923、01-制丝工房-电气照明921(出图)_t3、02-锅炉房-电气921(电子签名版)_t3_t3、03-扩建消控室
- run_takeoff.py / bench_takeoff.py：对 5 张图跑 extractor.takeoff.extract_takeoff 并和真值比对，结果写 results_takeoff.jsonl
先原样跑一遍得到当前基线，汇报里贴出来。当前已知：148 台中识别出 132 台；断路器数精确吻合仅 6 台，误差 10% 以内 83 台；照明大图约 84 秒。

## 要做的事
1. 漏柜：列出 16 台没识别出的柜及原因（图里有但没检出 / 柜号写法不同 / 在别的图纸里 / 真值里本来就不在这 5 张图），能修的修。
2. 断路器数量：按柜对比识别数与真值，按偏差原因归类（重复计数、漏读续行、“3x110A”这类数量写法、备用回路、控制电缆误计等），优先修影响柜数最多的前三类。
3. 已知具体问题：02YJ01/02YJ02 的检测信号线“市电故障检测:02AL01:N10”没读进；C01、C04、C08 回路数为 0；C01/C04/C08 有“回路名称/机械应急启动柜”“低压配电柜型号 GGD”“A 计算电流”这类表头文字被当成元器件（pi 会在 assemble 下游过滤，但源头在 takeoff 的表格解析，请从源头修）。
4. 控制电缆回路（KYJVP/KVV 等至操作柱、PLC，断路器为空）的识别规则已在 assemble.is_control_cable_circuit，takeoff 不要再为它们编造断路器字段。
5. T5：评估 native_takeoff.native_box_inventory（原生箱柜清单，原来接在已删的旧引擎里，现在只有测试在用）能否作为 takeoff 的补漏来源（系统图标题确认柜号 + 平面图柜号进待核对），能明显减少漏柜就接入 takeoff，否则说明理由。
6. 不得用真值去“对答案”式硬编码柜号或数量；所有改动必须是通用规则，并为每类修复加单元测试（用小构造数据，不要提交客户图纸和真值）。
7. 性能：照明大图不得比基线慢 20% 以上。

## 汇报
1 基线与修改后对比表（识别台数、断路器精确吻合台数、10% 内台数、各图耗时）2 16 台漏柜清单与原因 3 偏差归类及修了哪几类 4 T5 结论 5 pytest 数字 6 提交号 7 需要用户决定的事
