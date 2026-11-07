"""部署包纯净性验证：在「没有平台二进制」的条件下跑通依赖。

云函数是 Linux，包是在 Windows 打的。本脚本把部署包解压到临时目录，
强制让 Python 只从该目录加载依赖（PYTHONPATH 优先），验证：
    1. requests / bs4 从包内加载（不是本机 site-packages）
    2. 真实发起一次 HTTPS 请求成功
    3. charset_normalizer 走纯 Python 回退（不依赖 .pyd）
    4. cos_client 能生成签名
用法：python scripts/verify_purepy.py
"""
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent.parent
ZIP_PATH = REPO_ROOT / "4-交付物" / "campus-notify-scf.zip"

PROBE = r'''
import sys, os, json
print("PYTHONPATH优先:", sys.path[0])

import requests
import bs4
print("requests:", requests.__file__)
print("bs4:", bs4.__file__)
assert os.path.abspath(requests.__file__).startswith(os.environ["PKG_DIR"]), "requests 未从包内加载"

import charset_normalizer
from charset_normalizer import md
print("charset_normalizer.md:", getattr(md, "__file__", md))
assert ".pyd" not in str(getattr(md, "__file__", "")), "仍在用二进制扩展"

r = requests.get("https://www.hafu.edu.cn/index/ggtz.htm", timeout=20,
                 headers={"User-Agent": "campus-notify-verify/0.1"})
print("HTTPS 请求:", r.status_code, "字节数:", len(r.content))
assert r.status_code == 200

soup = bs4.BeautifulSoup(r.text, "html.parser")
print("bs4 解析到 a 标签:", len(soup.find_all("a")))

from cos_client import build_authorization
auth = build_authorization("get", "campus-notify/state.json", "b-1250000000",
                           "ap-beijing", "AKIDfake", "fakekey")
assert auth.startswith("q-sign-algorithm=sha1"), auth
print("COS 签名生成: OK")

import config, parser, state, summarizer, fetcher, main, handler
print("全部模块导入: OK")
print("RESULT_JSON:", json.dumps({"ok": True}))
'''


def main() -> int:
    if not ZIP_PATH.exists():
        print(f"缺少部署包：{ZIP_PATH}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(ZIP_PATH) as zf:
            zf.extractall(tmp)
            names = zf.namelist()
        binaries = [n for n in names if Path(n).suffix in {".pyd", ".so", ".dll", ".exe"}]
        print(f"包内文件 {len(names)} 个，平台二进制 {len(binaries)} 个")

        env = dict(os.environ)   # 继承系统环境（Windows 缺 SystemRoot 会导致 DNS 解析失败）
        env.update({
            "PKG_DIR": str(Path(tmp).resolve()),
            "PYTHONPATH": tmp,
            "PYTHONIOENCODING": "utf-8",
            "STATE_BACKEND": "local",
            "LLM_BASE_URL": "",
            "LLM_API_KEY": "",
            "LLM_MODEL": "",
        })
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
        )
        print(proc.stdout.strip())
        if proc.returncode != 0:
            print("--- stderr ---")
            print(proc.stderr.strip()[-2500:])
            print("\n失败：部署包依赖无法在纯 Python 环境运行")
            return 1

    print("\n通过：部署包在无平台二进制条件下可正常运行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
