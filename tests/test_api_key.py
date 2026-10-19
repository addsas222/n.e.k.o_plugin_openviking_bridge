"""API Key 解析：插件配置优先，为空时回退到 OpenViking 自己的 ovcli.conf。

回退的意义：用户不必把 key 抄一份到插件配置里。手动抄写既容易写坏文件
（PowerShell 5.1 的编码坑），key 轮换后两处也会不一致。
"""

from __future__ import annotations

import json

from ovb_client import default_cli_config_path, resolve_api_key


def _cli_conf(tmp_path, payload) -> object:
    path = tmp_path / "ovcli.conf"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_configured_key_wins(tmp_path) -> None:
    cli = _cli_conf(tmp_path, {"api_key": "from-cli"})
    assert resolve_api_key("from-config", cli) == ("from-config", "plugin config")


def test_falls_back_to_cli_conf(tmp_path) -> None:
    cli = _cli_conf(tmp_path, {"api_key": "from-cli"})
    assert resolve_api_key("", cli) == ("from-cli", "ovcli.conf")


def test_blank_configured_key_falls_back(tmp_path) -> None:
    cli = _cli_conf(tmp_path, {"api_key": "from-cli"})
    assert resolve_api_key("   ", cli) == ("from-cli", "ovcli.conf")


def test_missing_file_is_none(tmp_path) -> None:
    assert resolve_api_key("", tmp_path / "nope.conf") == ("", "none")


def test_malformed_json_is_none(tmp_path) -> None:
    path = tmp_path / "ovcli.conf"
    path.write_text("{not json", encoding="utf-8")
    assert resolve_api_key("", path) == ("", "none")


def test_missing_key_field_is_none(tmp_path) -> None:
    cli = _cli_conf(tmp_path, {"url": "http://127.0.0.1:1933"})
    assert resolve_api_key("", cli) == ("", "none")


def test_blank_key_value_is_none(tmp_path) -> None:
    cli = _cli_conf(tmp_path, {"api_key": "   "})
    assert resolve_api_key("", cli) == ("", "none")


def test_env_var_overrides_config_path(monkeypatch, tmp_path) -> None:
    target = tmp_path / "custom.conf"
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(target))
    assert default_cli_config_path() == target
