"""本地计划任务入口（无窗口运行）。

用途：Windows 计划任务每 30 分钟调起本脚本，静默抓取一轮。
      pythonw.exe 下没有控制台，sys.stdout/stderr 为 None，故先把输出重定向到日志文件，
      再导入 main（其模块级 logging.basicConfig 才能正常绑定输出）。

日志：2-代码/campus-notify/data/logs/crawl.log（超过 2MB 自动滚存为 .1）

手动测试：python scripts/local_run.py
"""
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "crawl.log"
MAX_LOG_BYTES = 2 * 1024 * 1024


def rotate() -> None:
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
        backup = LOG_FILE.with_suffix(".log.1")
        if backup.exists():
            backup.unlink()
        LOG_FILE.rename(backup)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate()
    log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log

    print(f"\n===== 计划任务启动 {datetime.now():%Y-%m-%d %H:%M:%S} =====")
    # 计划任务不传参数，清空 argv 让 main 的 argparse 走默认分支
    sys.argv = ["local_run.py"]
    try:
        import main as crawl_main

        code = crawl_main.main()
        print(f"===== 结束，退出码 {code} =====")
        return code
    except Exception:  # noqa: BLE001 计划任务里必须记录异常而不是静默退出
        import traceback

        traceback.print_exc()
        print("===== 异常退出 =====")
        return 1
    finally:
        log.flush()
        log.close()


if __name__ == "__main__":
    sys.exit(main())
