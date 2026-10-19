"""openviking_client 的纯逻辑与回环集成测试。

不需要 N.E.K.O 宿主：客户端只依赖标准库，测试里用一个本地 HTTP 假服务
验证真实请求路径、请求头与响应信封拆解，而不是只测字符串拼接。
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openviking_client import (  # noqa: E402
    OpenVikingClient,
    OpenVikingError,
    flatten_hits,
    format_hit,
    is_under,
    normalize_uri,
    safe_name,
    truncate,
)


def test_normalize_uri_collapses_segments() -> None:
    assert normalize_uri("viking://user//default/") == "viking://user/default"
    assert normalize_uri("viking://a/./b") == "viking://a/b"
    assert normalize_uri("viking://") == "viking://"


@pytest.mark.parametrize("bad", ["http://x/y", "viking://a/../b", ""])
def test_normalize_uri_rejects_unsafe(bad: str) -> None:
    with pytest.raises(OpenVikingError):
        normalize_uri(bad)


def test_is_under_respects_segment_boundary() -> None:
    root = normalize_uri("viking://user/default/memories")
    assert is_under(root, normalize_uri("viking://user/default/memories/note.md"))
    assert is_under(root, root)
    assert not is_under(root, normalize_uri("viking://user/default/memories-backup/x.md"))
    assert not is_under(root, normalize_uri("viking://user/default"))


def test_safe_name_keeps_one_segment() -> None:
    cleaned = safe_name("../../我的 记忆/note")
    assert "/" not in cleaned
    assert ".." not in cleaned
    assert cleaned != ""
    assert safe_name("   ") == "note"


def test_safe_name_supports_non_cjk_unicode() -> None:
    """\\w 是 Unicode 语义：日文假名、西里尔字母等不会被吞成空串。"""
    assert safe_name("メモ") == "メモ"
    assert safe_name("Заметка") == "Заметка"


def test_truncate_marks_original_length() -> None:
    assert truncate("abc", 10) == "abc"
    out = truncate("abcdef", 3)
    assert out.startswith("abc")
    assert "6" in out


def test_flatten_hits_merges_groups() -> None:
    payload = {
        "memories": [{"uri": "viking://user/default/memories/a.md", "level": 2}],
        "resources": [{"uri": "viking://resources/b.md", "level": 0}],
        "skills": [],
        "total": 2,
    }
    hits = flatten_hits(payload)
    assert [hit["group"] for hit in hits] == ["memories", "resources"]
    assert flatten_hits(None) == []


def test_format_hit_drops_heavy_fields() -> None:
    shaped = format_hit({"uri": "viking://x", "group": "memories", "level": 1, "score": 0.5, "embedding": [1, 2]})
    assert set(shaped) == {"uri", "group", "level", "score", "abstract"}


def test_client_rejects_non_http_base_url() -> None:
    with pytest.raises(OpenVikingError):
        OpenVikingClient("ftp://127.0.0.1:1933")


def test_request_guard_blocks_non_http_url() -> None:
    """纵深防御分支：即使 base_url 被绕过，拼装后的 URL 也必须仍是 http(s)。"""
    client = OpenVikingClient("http://127.0.0.1:1933")
    client.base_url = "file:///etc"
    with pytest.raises(OpenVikingError) as excinfo:
        client.health()
    assert "non-http" in str(excinfo.value)


class _Handler(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str]] = []

    def log_message(self, *args: object) -> None:  # 静音测试输出
        return

    def _reply(self, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen.append(("GET", self.path, self.headers.get("X-API-Key", "")))
        if self.path.startswith("/health"):
            self._reply({"status": "ok", "healthy": True, "version": "0.1.x", "auth_mode": "api_key"})
        elif self.path.startswith("/api/v1/content/read"):
            self._reply({"status": "ok", "result": "记忆正文"})
        else:
            self._reply({"status": "ok", "result": []})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        type(self).seen.append(("POST", self.path + " " + body, self.headers.get("X-API-Key", "")))
        self._reply(
            {
                "status": "ok",
                "result": {
                    "memories": [{"uri": "viking://user/default/memories/a.md", "level": 2, "score": 0.7}],
                    "resources": [],
                    "skills": [],
                    "total": 1,
                },
            }
        )


@pytest.fixture()
def fake_server():
    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _Handler
    finally:
        server.shutdown()
        server.server_close()


def test_client_round_trip_against_local_server(fake_server) -> None:
    base_url, handler = fake_server
    client = OpenVikingClient(base_url + "/", api_key="test-key", timeout_s=5.0)

    health = client.health()
    assert health["healthy"] is True
    assert health["version"] == "0.1.x"

    hits = client.find("部署流程", limit=3)
    assert len(hits) == 1
    assert hits[0]["group"] == "memories"

    assert client.read("viking://user/default/memories/a.md") == "记忆正文"

    assert handler.seen[0][0] == "GET"
    assert handler.seen[0][1].startswith("/health")
    assert handler.seen[0][2] == "test-key"
    assert "limit" in handler.seen[1][1]


def test_client_surfaces_server_error_status(fake_server, monkeypatch) -> None:
    base_url, handler = fake_server
    client = OpenVikingClient(base_url)

    def boom(self, payload):
        raw = json.dumps({"status": "error", "message": "nope"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    monkeypatch.setattr(handler, "_reply", boom, raising=False)
    with pytest.raises(OpenVikingError):
        client.health()


def test_unreachable_server_raises_clean_error() -> None:
    client = OpenVikingClient("http://127.0.0.1:1", timeout_s=1.0)
    with pytest.raises(OpenVikingError) as excinfo:
        client.health()
    assert "test-key" not in str(excinfo.value)
