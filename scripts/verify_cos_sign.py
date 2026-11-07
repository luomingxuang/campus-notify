"""COS 签名交叉验证：自研纯标准库签名 vs 官方 SDK 签名。

用途：确认 cos_client.build_authorization 与官方 cos-python-sdk-v5 逐字节一致，
      避免云函数里因签名错误导致状态读写失败。
用法：python scripts/verify_cos_sign.py
      需要本机装有官方 SDK（pip install cos-python-sdk-v5），仅本地验证用，不进部署包。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cos_client import build_authorization  # noqa: E402

FAKE_ID = "AKIDzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
FAKE_KEY = "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk"
BUCKET = "campus-notify-1250000000"
REGION = "ap-beijing"
KEY = "campus-notify/state.json"
FIXED_NOW = 1762437600


def sdk_authorization(method: str, key: str, body: bytes | None = None) -> tuple[str, dict]:
    from qcloud_cos.cos_auth import CosS3Auth
    from qcloud_cos.cos_client import CosConfig

    conf = CosConfig(Region=REGION, SecretId=FAKE_ID, SecretKey=FAKE_KEY)
    req = requests.Request(
        method=method,
        url=f"https://{BUCKET}.cos.{REGION}.myqcloud.com/{key}",
        data=body,
    ).prepare()
    if body is None:
        req.headers.pop("Content-Length", None)

    with patch("qcloud_cos.cos_auth.time.time", return_value=FIXED_NOW):
        CosS3Auth(conf=conf, key="/" + key, expire=9600)(req)
    return req.headers["Authorization"], dict(req.headers)


def parse_auth(auth: str) -> dict:
    return dict(kv.split("=", 1) for kv in auth.split("&"))


def compare(method: str, body: bytes | None) -> bool:
    sdk_auth, sdk_headers = sdk_authorization(method, KEY, body)

    signed = {}
    for name in sdk_headers:
        low = name.lower()
        if low in ("host", "content-type", "content-length"):
            signed[low] = sdk_headers[name]

    mine = build_authorization(
        method, KEY, BUCKET, REGION, FAKE_ID, FAKE_KEY,
        now=FIXED_NOW, expire=9600, extra_headers=signed,
    )

    a, b = parse_auth(sdk_auth), parse_auth(mine)
    ok = True
    for field in ("q-sign-time", "q-header-list", "q-signature"):
        if a[field] != b[field]:
            ok = False
            print(f"  ✗ {field} 不一致")
            print(f"     SDK : {a[field]}")
            print(f"     自研: {b[field]}")
    print(f"  {'✓' if ok else '✗'} {method.upper()} 签名{'一致' if ok else '不一致'}"
          f"（签名头: {b['q-header-list']}）")
    return ok


def main() -> int:
    print("交叉验证 COS 签名实现：")
    results = [compare("put", b'{"version": 1}'), compare("get", None)]
    if all(results):
        print("\n通过：自研签名与官方 SDK 完全一致，可安全用于云函数。")
        return 0
    print("\n失败：签名不一致，请勿部署。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
