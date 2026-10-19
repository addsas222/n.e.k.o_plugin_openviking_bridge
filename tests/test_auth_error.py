"""认证类错误的可读性测试。

401/403 几乎总是配置问题（api_key 没填、填了 root key）。报错必须直接说清楚
该改哪里，否则用户只看到一句 UNAUTHENTICATED，不知道从何下手。
这里直接替换 urlopen 抛 HTTPError，不需要起服务。
"""

from __future__ import annotations

import urllib.error
from io import BytesIO

import pytest
from ovb_client import OpenVikingClient, OpenVikingError


def _raise_http_error(code: int, body: bytes):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "err", {}, BytesIO(body))

    return fake_urlopen


@pytest.mark.parametrize("code", [401, 403])
def test_auth_error_points_at_api_key(monkeypatch, code: int) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _raise_http_error(code, b'{"error":{"code":"UNAUTHENTICATED"}}'),
    )
    client = OpenVikingClient("http://127.0.0.1:1933")
    with pytest.raises(OpenVikingError) as excinfo:
        client.health()
    message = str(excinfo.value)
    assert "api_key" in message
    assert "root key" in message
    assert str(code) in message


def test_other_http_error_keeps_generic_message(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", _raise_http_error(500, b"boom"))
    client = OpenVikingClient("http://127.0.0.1:1933")
    with pytest.raises(OpenVikingError) as excinfo:
        client.health()
    message = str(excinfo.value)
    assert "HTTP 500" in message
    assert "api_key" not in message
