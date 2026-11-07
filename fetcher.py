"""HTTP 抓取：限速、重试、统一 UA 与编码处理。"""
import time
import logging
import warnings

import requests
import urllib3

import config

# 官网证书链校验在部分环境不稳定，MVP 阶段关闭校验（仅抓公开页面，无敏感信息）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

logger = logging.getLogger("campus-notify.fetcher")

_session = requests.Session()
_session.headers.update({"User-Agent": config.UA})


def fetch(url: str, expect_ok: bool = True) -> str | None:
    """抓取 URL 返回文本；失败重试 RETRY_TIMES 次，仍失败返回 None 并记日志。"""
    for attempt in range(1 + config.RETRY_TIMES):
        try:
            time.sleep(config.REQUEST_DELAY_SECONDS)  # 限速，对官网友好
            resp = _session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS, verify=False)
            if expect_ok and resp.status_code != 200:
                raise requests.HTTPError(f"HTTP {resp.status_code} for {url}")
            resp.encoding = resp.apparent_encoding
            return resp.text
        except Exception as exc:  # noqa: BLE001 网络类异常统一记录
            logger.warning("抓取失败(第%d次) %s: %s", attempt + 1, url, exc)
    return None
