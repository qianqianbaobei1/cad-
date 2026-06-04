#!/usr/bin/env python3
"""
DWG 图纸解析工具
基于 LibreDWG (dwgread) + ezdxf，从 DWG 文件提取文本和图层信息。

依赖：
    brew install libredwg          # macOS
    pip install ezdxf

用法：
    # 单文件
    python3 dwg_parser.py 图纸.dwg

    # 指定输出目录
    python3 dwg_parser.py 图纸.dwg -o ./output

    # 批量解析整个目录
    python3 dwg_parser.py -b ./cad目录

    # 详细模式
    python3 dwg_parser.py 图纸.dwg -v
"""

import json
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime

# ── 配置 ──────────────────────────────────────────────
TIMEOUT_DWGREAD = 120       # dwgread 超时（秒），大文件可能需要更久
TIMEOUT_DWGLAYERS = 30
VERBOSE = False

# IDE 直接运行时使用的默认值（命令行参数优先）
# 设为 None 则必须传参数；设为路径字符串则 IDE 可无参直接跑
DEFAULT_DWG = "宿州302项目cad/1.建筑/20210906【三审修改建施打图版】S-1#、S-2#、S-3#、S-4#楼.dwg"
DEFAULT_OUTPUT = "./output/建筑"


def _decode_output(raw: bytes) -> str:
    """尝试多种编码解码命令行输出"""
    for enc in ["utf-8", "gbk", "gb2312", "gb18030", "latin-1"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def run_cmd(cmd: list, timeout: int, label: str = "") -> subprocess.CompletedProcess:
    """运行命令，统一错误处理"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        # 手动解码 stdout/stderr，兼容 GBK/UTF-8 混用
        setattr(result, "stdout", _decode_output(result.stdout))
        setattr(result, "stderr", _decode_output(result.stderr))
        if VERBOSE and result.stderr:
            err = result.stderr.strip()
            if err:
                for line in err.splitlines()[:10]:
                    print(f"  [{label}] {line}", file=sys.stderr)
        return result
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{label} 超时（{timeout}秒），文件可能过大")
    except FileNotFoundError:
        raise RuntimeError(
            f"找不到命令 '{cmd[0]}'，请先安装：brew install libredwg"
        )


def dwg_to_json(dwg_path: str, output_dir: str) -> Path:
    """dwgread -O JSON 导出（保留天正 TCH_TEXT）"""
    json_path = Path(output_dir) / f"{Path(dwg_path).stem}_dump.json"

    if json_path.exists() and Path(dwg_path).exists():
        if json_path.stat().st_mtime > Path(dwg_path).stat().st_mtime:
            print(f"  JSON 缓存有效，跳过转换")
            return json_path

    print(f"  正在转换 DWG → JSON ...")
    cmd = ["dwgread", "-O", "JSON", dwg_path, "-o", str(json_path)]
    run_cmd(cmd, TIMEOUT_DWGREAD, "dwgread JSON")

    if not json_path.exists():
        raise RuntimeError("dwgread 未生成 JSON 文件")

    size_mb = json_path.stat().st_size / (1024 * 1024)
    print(f"  JSON 生成完成: {size_mb:.1f} MB")
    return json_path


def get_layers(dwg_path: str) -> list:
    """dwglayers 获取图层列表"""
    print(f"  正在提取图层 ...")
    cmd = ["dwglayers", dwg_path]
    result = run_cmd(cmd, TIMEOUT_DWGLAYERS, "dwglayers")
    layers = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return layers


def extract_text_from_json(json_path: Path) -> list:
    """
    从 dwgread JSON 提取文本。
    天正自定义实体（TCH_TEXT 等）的文字在 text_value 字段中，
    标准 TEXT 实体在 text 字段中。
    """
    print(f"  正在解析 JSON ({json_path.stat().st_size / (1024*1024):.1f} MB) ...")

    with open(json_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    results = []
    seen = set()  # 去重

    def walk(obj, path="", depth=0):
        if depth > 20:
            return

        if isinstance(obj, dict):
            # 天正文字: text_value
            if "text_value" in obj and isinstance(obj["text_value"], str):
                text = obj["text_value"].strip()
                if text and len(text) >= 1:
                    sig = text[:80]
                    if sig not in seen:
                        seen.add(sig)
                        entry = {"text": text, "source": "text_value"}
                        if "layer" in obj:
                            entry["layer"] = str(obj["layer"])
                        results.append(entry)

            # 标准 TEXT: text（过滤 \A1; 格式标签）
            if "text" in obj and isinstance(obj["text"], str):
                t = obj["text"].strip()
                if t and not t.startswith("\\A") and len(t) >= 1:
                    sig = t[:80]
                    if sig not in seen:
                        seen.add(sig)
                        entry = {"text": t, "source": "text"}
                        if "layer" in obj:
                            entry["layer"] = str(obj["layer"])
                        results.append(entry)

            for k, v in obj.items():
                walk(v, f"{path}.{k}", depth + 1)

        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]", depth + 1)

    walk(data)
    print(f"  JSON 提取完成: {len(results)} 条文本")
    return results


def search_keywords(texts: list, keywords: list) -> dict:
    """在提取的文本中搜索关键词"""
    result = {}
    for kw in keywords:
        matches = []
        for t in texts:
            if kw in t["text"]:
                matches.append(t["text"])
        result[kw] = matches
    return result


def generate_report(
    dwg_path: str,
    layers: list,
    json_texts: list,
    keyword_results: dict,
    output_dir: str,
) -> Path:
    """生成文本报告"""
    stem = Path(dwg_path).stem
    report_path = Path(output_dir) / f"{stem}_解析报告.txt"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"DWG 图纸解析报告\n")
        f.write(f"{'=' * 60}\n")
        f.write(f"文件: {dwg_path}\n")
        f.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"文本数: {len(json_texts)}  图层数: {len(layers)}\n\n")

        f.write(f"【图层列表】\n{'-' * 40}\n")
        for layer in layers:
            f.write(f"  {layer}\n")
        f.write("\n")

        f.write(f"【关键词搜索】\n{'-' * 40}\n")
        for kw, matches in keyword_results.items():
            if matches:
                f.write(f"  [{kw}] 命中 {len(matches)} 条\n")
                for m in matches[:10]:
                    f.write(f"    - {m[:120]}\n")
                if len(matches) > 10:
                    f.write(f"    ... 还有 {len(matches) - 10} 条\n")
                f.write("\n")

        f.write(f"【全部文本】\n{'-' * 40}\n")
        for i, t in enumerate(json_texts):
            layer = t.get("layer", "?")
            src = t.get("source", "?")
            f.write(f"  [{i+1}] [{layer}] ({src}) {t['text'][:150]}\n")

    return report_path


def generate_summary_json(
    dwg_path: str,
    layers: list,
    json_texts: list,
    keyword_results: dict,
    output_dir: str,
) -> Path:
    """生成结构化 JSON 摘要"""
    stem = Path(dwg_path).stem
    json_path = Path(output_dir) / f"{stem}_摘要.json"

    summary = {
        "file": dwg_path,
        "parsed_at": datetime.now().isoformat(),
        "layers": layers,
        "layer_count": len(layers),
        "text_count": len(json_texts),
        "keyword_search": {
            kw: {"count": len(matches), "samples": matches[:10]}
            for kw, matches in keyword_results.items()
        },
        "text_samples": [t["text"] for t in json_texts[:100]],
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return json_path


# ── 主入口 ────────────────────────────────────────────

def parse_dwg(
    dwg_path: str,
    output_dir: str = None,
    keywords: list = None,
) -> dict:
    """
    解析一个 DWG 文件。

    参数:
        dwg_path: DWG 文件路径
        output_dir: 输出目录，默认与 DWG 同目录
        keywords: 要搜索的关键词

    返回:
        dict: {
            "layers": [...],
            "json_texts": [...],
            "keyword_results": {...},
            "report_path": "...",
            "summary_path": "...",
        }
    """
    if not os.path.exists(dwg_path):
        raise FileNotFoundError(f"文件不存在: {dwg_path}")
    if not dwg_path.lower().endswith(".dwg"):
        raise ValueError(f"不是 DWG 文件: {dwg_path}")

    if output_dir is None:
        output_dir = os.path.dirname(dwg_path) or "."
    os.makedirs(output_dir, exist_ok=True)

    if keywords is None:
        keywords = [
            "材料", "地面", "做法", "装修", "瓷砖", "地砖",
            "石材", "木地板", "地毯", "水泥", "砂浆",
            "防水", "找平", "抹灰", "涂料", "吊顶",
        ]

    print(f"\n文件: {os.path.basename(dwg_path)}")
    print(f"{'=' * 50}")

    # Step 1: 图层
    layers = get_layers(dwg_path)
    print(f"  图层数: {len(layers)}")

    # Step 2: JSON（含天正文字）
    json_path = dwg_to_json(dwg_path, output_dir)
    json_texts = extract_text_from_json(json_path)

    # Step 3: 搜索 & 报告
    keyword_results = search_keywords(json_texts, keywords)
    report_path = generate_report(
        dwg_path, layers, json_texts, keyword_results, output_dir
    )
    summary_path = generate_summary_json(
        dwg_path, layers, json_texts, keyword_results, output_dir
    )

    # 摘要输出
    print(f"\n  报告: {report_path}")
    print(f"  摘要: {summary_path}")
    for kw, matches in keyword_results.items():
        if matches:
            print(f"  [{kw}] {len(matches)} 条")

    return {
        "layers": layers,
        "json_texts": json_texts,
        "keyword_results": keyword_results,
        "report_path": str(report_path),
        "summary_path": str(summary_path),
    }


def batch_parse(input_dir: str, output_dir: str = None) -> list:
    """批量解析目录下所有 DWG 文件"""
    import glob

    dwg_files = sorted(glob.glob(os.path.join(input_dir, "*.dwg")))
    if not dwg_files:
        print(f"未找到 DWG 文件: {input_dir}")
        return []

    if output_dir is None:
        output_dir = os.path.join(input_dir, "_parsed")
    os.makedirs(output_dir, exist_ok=True)

    results = []
    for i, dwg in enumerate(dwg_files):
        print(f"\n[{i+1}/{len(dwg_files)}] {os.path.basename(dwg)}")
        try:
            r = parse_dwg(dwg, output_dir)
            results.append({"file": dwg, **r})
        except Exception as e:
            print(f"  失败: {e}")
            results.append({"file": dwg, "error": str(e)})

    # 批量摘要
    summary = {
        "input_dir": input_dir,
        "total": len(dwg_files),
        "success": sum(1 for r in results if "error" not in r),
        "failed": sum(1 for r in results if "error" in r),
        "results": [
            {
                "file": r.get("file", ""),
                "layers": len(r.get("layers", [])),
                "texts": len(r.get("json_texts", [])),
                "hits": {kw: len(v) for kw, v in r.get("keyword_results", {}).items() if v},
            }
            if "error" not in r
            else {"file": r["file"], "error": r["error"]}
            for r in results
        ],
    }

    sp = os.path.join(output_dir, "_批量摘要.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n完成: {summary['success']}/{summary['total']}")
    print(f"摘要: {sp}")
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DWG 图纸解析工具")
    parser.add_argument("dwg_path", nargs="?", help="DWG 文件路径")
    parser.add_argument("-o", "--output-dir", default=None, help="输出目录")
    parser.add_argument("-b", "--batch", help="批量解析：指定目录路径")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")

    args = parser.parse_args()
    VERBOSE = args.verbose

    if args.batch:
        batch_parse(args.batch, args.output_dir)
    elif args.dwg_path:
        parse_dwg(args.dwg_path, args.output_dir)
    elif DEFAULT_DWG:
        print(f"使用默认文件: {DEFAULT_DWG}")
        parse_dwg(DEFAULT_DWG, args.output_dir or DEFAULT_OUTPUT)
    else:
        parser.print_help()
        sys.exit(1)
