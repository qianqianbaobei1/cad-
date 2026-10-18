# -*- coding: utf-8 -*-
"""CAD 格式转换引擎运行时自动引导与适配模块 (LibreDWG & acadrust).

本模块负责在容器/宿主环境冷启动时：
1. 自动探针检测 dwg2dxf / dwg2SVG / extractor-dwg 可用性；
2. 若缺少 Rust acadrust 工具且存在 cargo，自动执行轻量编译 (约 20 秒)；
3. 若缺少 LibreDWG 且存在构建环境 (gcc/make/curl)，自动后台编译静态 dwg2dxf/dwg2SVG；
4. 动态注入环境变量并打通 cad.py 与 cad_fallback.py，实现双引擎无感兜底。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TMP_BIN_DIR = Path("/tmp/libredwg/bin")


def test_binary(path: str | Path, *args: str) -> bool:
    """测试指定二进制程序是否可正常执行。"""
    try:
        p = Path(path)
        if not p.is_file() or not os.access(p, os.X_OK):
            return False
        res = subprocess.run([str(p)] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def probe_dwg2dxf() -> str | None:
    """探测可用的 dwg2dxf 路径。"""
    candidates = [
        os.environ.get("EXTRACTOR_DWG2DXF_BIN"),
        str(TMP_BIN_DIR / "dwg2dxf"),
        "/tmp/bin/dwg2dxf",
        str(REPO_ROOT / "backend/bin/dwg2dxf"),
        str(REPO_ROOT / "bin/dwg2dxf"),
        "/usr/local/bin/dwg2dxf",
        "/opt/homebrew/bin/dwg2dxf",
        "/usr/bin/dwg2dxf",
        shutil.which("dwg2dxf"),
    ]
    for c in candidates:
        if c and test_binary(c, "--version"):
            return os.path.abspath(c)
    return None


def probe_dwg2svg() -> str | None:
    """探测可用的 dwg2SVG / dwg2svg 路径。"""
    candidates = [
        os.environ.get("EXTRACTOR_DWG2SVG_BIN"),
        str(TMP_BIN_DIR / "dwg2SVG"),
        str(TMP_BIN_DIR / "dwg2svg"),
        "/tmp/bin/dwg2SVG",
        "/tmp/bin/dwg2svg",
        str(REPO_ROOT / "backend/bin/dwg2SVG"),
        str(REPO_ROOT / "backend/bin/dwg2svg"),
        "/usr/local/bin/dwg2SVG",
        "/usr/local/bin/dwg2svg",
        "/opt/homebrew/bin/dwg2SVG",
        "/opt/homebrew/bin/dwg2svg",
        "/usr/bin/dwg2SVG",
        "/usr/bin/dwg2svg",
        shutil.which("dwg2SVG"),
        shutil.which("dwg2svg"),
    ]
    for c in candidates:
        if c and test_binary(c, "--version"):
            return os.path.abspath(c)
    return None


def probe_acadrust() -> str | None:
    """探测可用的 extractor-dwg (acadrust) 路径。"""
    candidates = [
        os.environ.get("EXTRACTOR_ACADRUST_BIN"),
        str(REPO_ROOT / "integrations/acadrust/target/release/extractor-dwg"),
        "/tmp/bin/extractor-dwg",
        str(REPO_ROOT / "bin/extractor-dwg"),
        shutil.which("extractor-dwg"),
    ]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return os.path.abspath(c)
    return None


def build_acadrust_if_needed() -> str | None:
    """若 extractor-dwg 不存在且 cargo 可用，自动执行编译。"""
    existing = probe_acadrust()
    if existing:
        return existing

    cargo_bin = shutil.which("cargo")
    manifest = REPO_ROOT / "integrations/acadrust/Cargo.toml"
    if not cargo_bin or not manifest.is_file():
        return None

    print("[CAD Bootstrap] 检测到 cargo，正在编译本地 extractor-dwg 转换器...")
    try:
        proc = subprocess.run(
            [cargo_bin, "build", "--release", "--manifest-path", str(manifest)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
            check=False,
        )
        if proc.returncode == 0:
            target_bin = REPO_ROOT / "integrations/acadrust/target/release/extractor-dwg"
            if target_bin.is_file() and os.access(target_bin, os.X_OK):
                print(f"[CAD Bootstrap] extractor-dwg 编译成功: {target_bin}")
                os.environ["EXTRACTOR_ACADRUST_BIN"] = str(target_bin)
                return str(target_bin)
        else:
            print(f"[CAD Bootstrap] cargo 编译失败 (code={proc.returncode}): {proc.stderr[:300]}")
    except Exception as exc:
        print(f"[CAD Bootstrap] 编译 extractor-dwg 异常: {exc}")
    return None


def _build_libredwg_worker() -> None:
    """后台编译 GNU LibreDWG 并安装到 /tmp/libredwg/bin。"""
    if probe_dwg2dxf():
        return

    gcc_bin = shutil.which("gcc") or shutil.which("clang")
    make_bin = shutil.which("make")
    curl_bin = shutil.which("curl")
    if not (gcc_bin and make_bin and curl_bin):
        return

    src_dir = Path("/tmp/libredwg_build")
    tar_path = Path("/tmp/libredwg-0.14.tar.gz")
    TMP_BIN_DIR.mkdir(parents=True, exist_ok=True)

    print("[CAD Bootstrap] 开始在后台准备 GNU LibreDWG 运行时环境...")
    mirrors = [
        "https://mirror.twds.com.tw/gnu/libredwg/libredwg-0.14.tar.gz",
        "https://ftpmirror.gnu.org/libredwg/libredwg-0.14.tar.gz",
        "https://ftp.gnu.org/gnu/libredwg/libredwg-0.14.tar.gz",
    ]

    downloaded = False
    if tar_path.is_file() and tar_path.stat().st_size > 10_000_000:
        downloaded = True
    else:
        for m in mirrors:
            try:
                res = subprocess.run([curl_bin, "-sSL", "--connect-timeout", "10", m, "-o", str(tar_path)], timeout=60)
                if res.returncode == 0 and tar_path.is_file() and tar_path.stat().st_size > 10_000_000:
                    downloaded = True
                    break
            except Exception:
                continue

    if not downloaded:
        print("[CAD Bootstrap] 下载 LibreDWG 源码归档超时或失败，保持 acadrust 单引擎运行")
        return

    try:
        src_dir.mkdir(parents=True, exist_ok=True)
        # 解压
        subprocess.run(["tar", "-zxf", str(tar_path), "-C", str(src_dir), "--strip-components=1"], check=True, timeout=30)
        # 配置 (关闭繁重 bindings 与 docs，极速构建)
        conf_cmd = [
            str(src_dir / "configure"),
            f"--prefix=/tmp/libredwg",
            "--disable-bindings",
            "--disable-docs",
            "--disable-dependency-tracking",
            "--disable-shared",
            "--enable-static",
            "--enable-silent-rules",
        ]
        res_conf = subprocess.run(conf_cmd, cwd=str(src_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if res_conf.returncode != 0:
            print(f"[CAD Bootstrap] LibreDWG configure 失败: {res_conf.stderr.decode(errors='replace')[:300]}")
            return

        # 编译 src 库与 dwg2dxf / dwg2SVG
        import multiprocessing
        nproc = str(max(1, multiprocessing.cpu_count()))
        subprocess.run([make_bin, "-C", "src", f"-j{nproc}"], cwd=str(src_dir), check=True, timeout=300)
        subprocess.run([make_bin, "-C", "programs", "dwg2dxf", "dwg2SVG"], cwd=str(src_dir), check=True, timeout=120)

        # 拷贝产物至 TMP_BIN_DIR
        built_dwg2dxf = src_dir / "programs/dwg2dxf"
        built_dwg2svg = src_dir / "programs/dwg2SVG"
        if built_dwg2dxf.is_file():
            shutil.copy2(str(built_dwg2dxf), str(TMP_BIN_DIR / "dwg2dxf"))
            os.chmod(str(TMP_BIN_DIR / "dwg2dxf"), 0o755)
            os.environ["EXTRACTOR_DWG2DXF_BIN"] = str(TMP_BIN_DIR / "dwg2dxf")
            print(f"[CAD Bootstrap] GNU LibreDWG dwg2dxf 编译就绪: {TMP_BIN_DIR / 'dwg2dxf'}")
        if built_dwg2svg.is_file():
            shutil.copy2(str(built_dwg2svg), str(TMP_BIN_DIR / "dwg2SVG"))
            os.chmod(str(TMP_BIN_DIR / "dwg2SVG"), 0o755)
            os.environ["EXTRACTOR_DWG2SVG_BIN"] = str(TMP_BIN_DIR / "dwg2SVG")
    except Exception as exc:
        print(f"[CAD Bootstrap] 后台编译 LibreDWG 过程捕获异常: {exc}")


def bootstrap_cad_converters() -> dict[str, str | None]:
    """引导并校验所有 CAD 转换工具状态。"""
    # 1. 优先探测已有 dwg2dxf
    dwg2dxf_tool = probe_dwg2dxf()
    if dwg2dxf_tool:
        os.environ["EXTRACTOR_DWG2DXF_BIN"] = dwg2dxf_tool

    # 2. 探测或构建 extractor-dwg (acadrust)
    acadrust_tool = probe_acadrust()
    if not acadrust_tool:
        acadrust_tool = build_acadrust_if_needed()
    if acadrust_tool:
        os.environ["EXTRACTOR_ACADRUST_BIN"] = acadrust_tool

    # 3. 探测 dwg2svg
    dwg2svg_tool = probe_dwg2svg()
    if dwg2svg_tool:
        os.environ["EXTRACTOR_DWG2SVG_BIN"] = dwg2svg_tool

    # 4. 若尚未就绪 dwg2dxf，启动后台构建线程
    if not dwg2dxf_tool:
        t = threading.Thread(target=_build_libredwg_worker, name="cad-bootstrap-libredwg", daemon=True)
        t.start()

    status = {
        "dwg2dxf": dwg2dxf_tool or ("building_in_background" if shutil.which("gcc") else None),
        "acadrust": acadrust_tool,
        "dwg2svg": dwg2svg_tool,
    }
    print(f"[CAD Bootstrap] CAD 转换引擎装载状态: {status}")
    return status


if __name__ == "__main__":
    st = bootstrap_cad_converters()
    print("Bootstrap result:", st)
