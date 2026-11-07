"""打包腾讯云函数部署 zip。

用法：
    python scripts/build_scf_package.py

产物：4-交付物/campus-notify-scf.zip（代码 + 依赖，可直接上传 SCF）

跨平台要点（踩过的坑）：
    云函数运行在 Linux（Python 3.10），在 Windows 上 pip install -t 装出来的是
    win_amd64 的 .pyd 二进制，拷到 Linux 上必然加载失败。因此：
    1. 不引入任何必须有 C 扩展的包（COS 用自研纯标准库客户端 cos_client.py）
    2. 打包时剔除所有 .pyd/.so/.dll/.exe，让纯 Python 回退实现生效
    3. 这些包的纯 Python 回退已本地验证（见 scripts/verify_purepy.py）
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent          # campus-notify/
REPO_ROOT = PROJECT_ROOT.parent.parent                          # 项目根目录
BUILD_DIR = PROJECT_ROOT / "_build"
OUTPUT_ZIP = REPO_ROOT / "4-交付物" / "campus-notify-scf.zip"

CODE_FILES = [
    "handler.py", "main.py", "config.py", "fetcher.py",
    "parser.py", "state.py", "summarizer.py", "cos_client.py",
]

# 只保留纯 Python 依赖；COS 走自研 cos_client.py，不需要 cos-python-sdk-v5
REQUIREMENTS = ["requests", "beautifulsoup4"]

BINARY_SUFFIXES = {".pyd", ".so", ".dylib", ".dll", ".exe", ".a"}


def install_deps(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "-q",
           "--target", str(target), "--upgrade", *REQUIREMENTS]
    print("安装依赖：", " ".join(REQUIREMENTS))
    subprocess.run(cmd, check=True)


def assemble() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)
    for name in CODE_FILES:
        src = PROJECT_ROOT / name
        if not src.exists():
            raise FileNotFoundError(f"缺少源文件：{src}")
        shutil.copy2(src, BUILD_DIR / name)
    install_deps(BUILD_DIR)


def skipped(path: Path) -> bool:
    if path.is_dir():
        return True
    if "__pycache__" in path.parts or path.suffix == ".pyc":
        return True
    if path.suffix in BINARY_SUFFIXES:
        return True
    if "bin" in path.relative_to(BUILD_DIR).parts[:1]:
        return True   # wheel 里的 console scripts，云函数用不到
    return False


def zip_package() -> None:
    OUTPUT_ZIP.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()
    removed = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(BUILD_DIR.rglob("*")):
            if skipped(path):
                if not path.is_dir():
                    removed += 1
                continue
            zf.write(path, path.relative_to(BUILD_DIR))
    size_mb = OUTPUT_ZIP.stat().st_size / 1024 / 1024
    print(f"打包完成：{OUTPUT_ZIP}（{size_mb:.1f} MB，剔除平台二进制 {removed} 个）")
    if size_mb > 40:
        print("⚠️ 超过 40MB 建议改用「依赖层」方式上传，见部署说明")


def main() -> int:
    assemble()
    zip_package()
    return 0


if __name__ == "__main__":
    sys.exit(main())
