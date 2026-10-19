"""OpenViking HTTP 客户端（纯标准库）。

只依赖 urllib / json / re，不 import N.E.K.O SDK：
这样在没装宿主的机器上也能直接跑 pytest，逻辑可独立验证。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

SCHEME = "viking://"
_ALLOWED_URL_SCHEMES = ("http://", "https://")
# \\w（Unicode 语义）覆盖中日韩、拉丁、西里尔等各类字母与数字；
# 显式保留 "-"，其余（含 "/" "." 空格）一律换成 "-"，因此路径穿越仍然被挡住。
_SAFE_NAME_RE = re.compile(r"[^\w-]+")
_GROUPS = ("memories", "resources", "skills")


class OpenVikingError(RuntimeError):
    """调用 OpenViking 失败。消息已脱敏，不含 API Key。"""


def normalize_uri(uri: str) -> str:
    """把 viking:// 路径收敛成规范形式：去掉空段、"." 与尾斜杠。"""
    if not isinstance(uri, str):
        raise OpenVikingError("uri must be a string")
    text = uri.strip()
    if not text.startswith(SCHEME):
        raise OpenVikingError("uri must start with " + SCHEME)
    segments = [seg for seg in text[len(SCHEME) :].split("/") if seg not in ("", ".")]
    if ".." in segments:
        raise OpenVikingError("uri must not contain ..")
    return SCHEME + "/".join(segments)


def is_under(root: str, uri: str) -> bool:
    """uri 是否等于 root 或位于 root 之下（两者都需先 normalize_uri）。"""
    if uri == root:
        return True
    return uri.startswith(root.rstrip("/") + "/")


def safe_name(name: str) -> str:
    """把用户给的名字收敛成单个安全的文件名片段。"""
    cleaned = _SAFE_NAME_RE.sub("-", (name or "").strip()).strip("-")
    return cleaned or "note"


def truncate(text: str, limit: int) -> str:
    """按字符数截断，并在末尾标注原文长度。"""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n…（已截断，原文 " + str(len(text)) + " 字符）"


def flatten_hits(result: object) -> list[dict]:
    """把 search/find 的分组响应（memories/resources/skills）压成一维命中列表。"""
    if not isinstance(result, dict):
        return []
    hits: list[dict] = []
    for group in _GROUPS:
        items = result.get(group)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                hits.append({**item, "group": group})
    return hits


def format_hit(hit: dict, max_chars: int = 2000) -> dict:
    """把一条命中裁成面板与模型都好读的形状：路径、层级、分数、摘要。"""
    return {
        "uri": str(hit.get("uri", "")),
        "group": str(hit.get("group", "")),
        "level": hit.get("level"),
        "score": hit.get("score"),
        "abstract": truncate(str(hit.get("abstract") or ""), max_chars),
    }


class OpenVikingClient:
    """一个极小的 OpenViking HTTP 客户端：health / find / read / ls / write。"""

    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 30.0) -> None:
        url = (base_url or "").strip().rstrip("/")
        if not url.lower().startswith(_ALLOWED_URL_SCHEMES):
            raise OpenVikingError("base_url must start with http:// or https://")
        self.base_url = url
        self.timeout_s = float(timeout_s)
        self._api_key = api_key or ""

    def _request(self, method: str, path: str, *, params=None, body=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        # 纵深防御：base_url 已在 __init__ 里限定协议，这里再验一次最终 URL，
        # 确保任何拼装路径都不可能把请求引到 file:// 之类的本地协议。
        if not url.lower().startswith(_ALLOWED_URL_SCHEMES):
            raise OpenVikingError("refusing to open a non-http(s) URL")
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # urlopen 的协议已由上面两道 http(s) 白名单校验兜住，
            # 并有 test_request_guard_blocks_non_http_url 覆盖该分支。
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # nosec B310
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            if exc.code in (401, 403):
                # 这两类错误几乎总是配置问题，直接说清楚该改哪里，
                # 否则用户只会看到一句 UNAUTHENTICATED，不知道从何下手。
                raise OpenVikingError(
                    "OpenViking 拒绝了这次请求（HTTP "
                    + str(exc.code)
                    + "）。请在插件配置的 [openviking] 段填写 api_key —— "
                    + "要用 user/admin key，root key 只能管后台、无法读写记忆。"
                    + "服务端返回："
                    + detail
                ) from None
            raise OpenVikingError("HTTP " + str(exc.code) + " from " + path + ": " + detail) from None
        except urllib.error.URLError as exc:
            raise OpenVikingError("cannot reach OpenViking at " + self.base_url + ": " + str(exc.reason)) from None
        except TimeoutError:
            raise OpenVikingError("OpenViking request timed out: " + path) from None
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise OpenVikingError("non-JSON response from " + path) from None
        if isinstance(payload, dict):
            status = payload.get("status")
            if status not in (None, "ok"):
                raise OpenVikingError("OpenViking returned status=" + str(status) + " for " + path)
            return payload.get("result", payload)
        return payload

    def health(self) -> dict:
        result = self._request("GET", "/health")
        return result if isinstance(result, dict) else {"raw": result}

    def find(self, query: str, limit: int = 5, target_uri: str = "") -> list[dict]:
        body: dict = {"query": query, "limit": int(limit)}
        if target_uri:
            body["target_uri"] = target_uri
        return flatten_hits(self._request("POST", "/api/v1/search/find", body=body))

    def read(self, uri: str) -> str:
        result = self._request("GET", "/api/v1/content/read", params={"uri": uri})
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    def ls(self, uri: str, limit: int = 50) -> list[dict]:
        result = self._request("GET", "/api/v1/fs/ls", params={"uri": uri, "simple": "true", "limit": int(limit)})
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            entries = result.get("entries") or result.get("result") or []
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, dict)]
        return []

    def write(self, uri: str, content: str) -> dict:
        result = self._request(
            "POST", "/api/v1/content/write", body={"uri": uri, "content": content, "mode": "replace"}
        )
        return result if isinstance(result, dict) else {}
