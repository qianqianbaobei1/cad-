# CAD 内容提取与材料拆解系统

当前项目已经有统一运行入口：

```bash
python3 运行入口.py
```

默认运行内容：

```text
已有 BOQ Excel + 已有 CAD 做法表 JSON
-> 项目知识库
-> 材料拆解工作区
-> 采购计划物料清单
```

默认不会重新跑 DWG 解析，因为 DWG 解析耗时较长，并且可能依赖 `dwgread`、LLM API Key 等外部环境。

## 常用命令

查看当前项目状态：

```bash
python3 运行入口.py --stage status
```

运行默认主流程：

```bash
python3 运行入口.py --stage all
```

最终交付文件只有一份：

```text
projects/<项目名>/最终输出/采购计划物料清单.xlsx
```

只重新构建项目知识库：

```bash
python3 运行入口.py --stage knowledge
```

只重新构建材料拆解工作区：

```bash
python3 运行入口.py --stage workspace
```

只重新生成最终采购计划物料清单：

```bash
python3 运行入口.py --stage procurement
```

预览 CAD 文件，不真正处理：

```bash
python3 运行入口.py --stage cad --dry-run
```

重新跑 CAD 抽取，但跳过 LLM：

```bash
python3 运行入口.py --stage cad --skip-llm
```

指定项目：

```bash
python3 运行入口.py --project 宿州302 --stage all
```

## 目录结构

```text
projects/<项目名>/原始输入/             放 BOQ Excel
projects/<项目名>/提取结果/做法表/       放 CAD 做法表 JSON
projects/<项目名>/项目知识库/            系统构建的项目上下文
projects/<项目名>/材料拆解工作区/        拆解任务、缺失资料、人工确认 Excel
projects/<项目名>/最终输出/采购计划物料清单.xlsx   最终交付文件
projects/<项目名>/运行报告.json          最近一次统一入口运行摘要
```

## 当前主流程边界

现在的对外交付产出只看 `最终输出/采购计划物料清单.xlsx`。

项目知识库、材料拆解工作区、缺失资料清单等文件是系统内部过程数据，不作为对外交付物。
