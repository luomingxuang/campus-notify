"""极简腾讯云 COS 客户端（纯标准库实现，无二进制依赖）。

为什么不用官方 cos-python-sdk-v5：
    官方 SDK 依赖 pycryptodome（C 扩展）。在 Windows 上打的包拷到 Linux 云函数里，
    .pyd 无法加载，签名直接失败。本模块只用 hashlib/hmac/requests 实现签名，
    跨平台安全，同时包体积从 8MB 降到 1MB 量级。

只实现状态快照需要的两个动作：get_object / put_object。
签名算法与官方 SDK 完全一致（已交叉验证，见 verify_cos_sign.py）。
"""
import hashlib
import hmac
import logging
import time
from urllib.parse import quote

import requests

logger = logging.getLogger("campus-notify.cos")

SIGN_EXPIRE_SECONDS = 600


class CosError(Exception):
    """COS 调用失败。"""


def _host(bucket: str, region: str) -> str:
    return f"{bucket}.cos.{region}.myqcloud.com"


def build_authorization(
    method: str,
    key: str,
    bucket: str,
    region: str,
    secret_id: str,
    secret_key: str,
    now: int | None = None,
    expire: int = SIGN_EXPIRE_SECONDS,
    extra_headers: dict | None = None,
) -> str:
    """生成 COS 请求签名（q-sign-algorithm=sha1）。

    与官方 cos_auth.CosS3Auth 保持一致的算法：
        HttpString = method\\n path \\n params \\n signHeaders \\n
        StringToSign = "sha1"\\n signTime \\n sha1(HttpString) \\n
        Signature = hmac_sha1(hmac_sha1(secret_key, signTime), StringToSign)
    仅对 host 头签名（COS 允许局部签名，未列入 q-header-list 的头不参与校验）。
    """
    path = "/" + key.lstrip("/")
    host = _host(bucket, region)

    if now is None:
        now = int(time.time())
    sign_time = f"{now - 60};{now + expire}"

    header_str = f"host={quote(host, safe='-_.~')}"
    signed_keys = ["host"]
    for hk, hv in (extra_headers or {}).items():
        k = hk.lower()
        header_str += f"&{k}={quote(str(hv), safe='-_.~')}"
        signed_keys.append(k)
    pairs = sorted(header_str.split("&"))
    header_str = "&".join(pairs)
    signed_keys = sorted(signed_keys)

    http_string = f"{method.lower()}\n{path}\n\n{header_str}\n"

    sha1_hex = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
    string_to_sign = f"sha1\n{sign_time}\n{sha1_hex}\n"

    sign_key = hmac.new(
        secret_key.encode("utf-8"), sign_time.encode("utf-8"), hashlib.sha1
    ).hexdigest()
    signature = hmac.new(
        sign_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1
    ).hexdigest()

    header_list = ";".join(signed_keys)
    return (
        f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={sign_time}"
        f"&q-key-time={sign_time}&q-header-list={header_list}&q-url-param-list=&q-signature={signature}"
    )


class CosClient:
    """最小可用的 COS 客户端。"""

    def __init__(self, secret_id: str, secret_key: str, region: str, bucket: str,
                 timeout: int = 10):
        if not all([secret_id, secret_key, region, bucket]):
            raise CosError("COS 配置不完整（SecretId/SecretKey/Region/Bucket）")
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.region = region
        self.bucket = bucket
        self.timeout = timeout
        self._session = requests.Session()

    def _url(self, key: str) -> str:
        return f"https://{_host(self.bucket, self.region)}/{quote(key.lstrip('/'), safe='/')}"

    def _headers(self, method: str, key: str, sign_headers: dict | None = None,
                 send_headers: dict | None = None) -> dict:
        headers = {
            "Authorization": build_authorization(
                method, key, self.bucket, self.region, self.secret_id, self.secret_key,
                extra_headers=sign_headers,
            ),
            "Host": _host(self.bucket, self.region),
        }
        if send_headers:
            headers.update(send_headers)
        return headers

    def put_object(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
        send = {"Content-Type": content_type}
        resp = self._session.put(
            self._url(key),
            data=body,
            headers=self._headers("put", key, sign_headers={"content-type": content_type},
                                  send_headers=send),
            timeout=self.timeout,
        )
        if resp.status_code >= 300:
            raise CosError(
                f"COS PUT 失败 {resp.status_code}：{resp.text[:300]}"
            )

    def get_object(self, key: str) -> bytes | None:
        """读取对象；对象不存在返回 None（首次运行属正常情况）。"""
        resp = self._session.get(
            self._url(key), headers=self._headers("get", key), timeout=self.timeout
        )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 300:
            raise CosError(
                f"COS GET 失败 {resp.status_code}：{resp.text[:300]}"
            )
        return resp.content

    # ---- 桶管理（部署脚本使用）----

    def bucket_exists(self) -> bool:
        resp = self._session.head(
            f"https://{_host(self.bucket, self.region)}/",
            headers=self._headers("head", ""),
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        if resp.status_code in (403,):
            # 无 ListBucket 权限的密钥访问自己的桶也会返回 403，视为存在
            return True
        raise CosError(f"COS HEAD Bucket 失败 {resp.status_code}：{resp.text[:200]}")

    def create_bucket(self, acl: str = "private") -> bool:
        """创建私有桶。返回 True 表示新建成功，False 表示已存在。

        注意：COS 要求所有 x-cos-* 请求头必须参与签名，故 x-cos-acl 一并签。
        """
        resp = self._session.put(
            f"https://{_host(self.bucket, self.region)}/",
            data=b"",
            headers=self._headers(
                "put", "",
                sign_headers={"content-type": "application/xml", "x-cos-acl": acl},
                send_headers={"Content-Type": "application/xml", "x-cos-acl": acl},
            ),
            timeout=self.timeout,
        )
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 409:   # BucketAlreadyOwnedByYou / BucketAlreadyExists
            return False
        raise CosError(f"COS 建桶失败 {resp.status_code}：{resp.text[:300]}")
