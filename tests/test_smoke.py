"""清单自测：只读 plugin.toml 与 __init__.py 文本，不 import SDK。

文件名沿用 N.E.K.O 插件脚手架约定（tests/test_smoke.py），`neko-plugin check -r`
要求它存在。市场插件仓库里没有装 N.E.K.O，直接 import 插件模块会
ModuleNotFoundError，所以这里只做文本级一致性校验（入口声明 vs 装饰器）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin.toml"
SOURCE = ROOT / "__init__.py"

ENTRY_DECL = re.compile(r'\[\[plugin\.entries\]\][^\[]*?id = "([^"]+)"', re.S)
ENTRY_DECOR = re.compile(r'@plugin_entry\([^)]*?id="([^"]+)"', re.S)
TOOL_DECOR = re.compile(r'@llm_tool\([^)]*?name="([^"]+)"', re.S)


def _manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def _source_text() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_plugin_manifest_exists() -> None:
    text = _manifest_text()
    assert 'id = "openviking_bridge"' in text
    assert 'entry = "plugin.plugins.openviking_bridge:OpenVikingBridgePlugin"' in text


def test_declared_entries_match_decorators() -> None:
    declared = set(ENTRY_DECL.findall(_manifest_text()))
    decorated = set(ENTRY_DECOR.findall(_source_text()))
    assert declared, "plugin.toml 没有声明任何入口"
    assert declared == decorated, f"声明与装饰器不一致: {declared ^ decorated}"


def test_llm_tool_names_are_url_safe() -> None:
    names = TOOL_DECOR.findall(_source_text())
    assert names, "没有注册任何 LLM 工具"
    for name in names:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", name), name


def test_no_hardcoded_api_key_in_repo() -> None:
    text = _manifest_text()
    assert 'api_key = ""' in text, "plugin.toml 里的 api_key 必须是空串"
    assert "sk-" not in text
