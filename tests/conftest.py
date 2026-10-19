"""把插件自身的客户端模块载入测试进程。

openviking_client 不 import 宿主 SDK，但测试文件直接写
`from openviking_client import ...` 会让 ruff 的导入分组随工作目录漂移：
在插件目录下跑（本地）能解析到文件，算「第一方」；官方 CI 从仓库父目录跑，
解析不到，算「第三方」——两边要求相反，无法同时满足。

这里按路径把它注册成 ovb_client：这个名字在插件目录下没有对应路径，
所以本地与 CI 都会稳定地归为第三方，排序结果一致。
同目录的 plugin_knowledge_base 用同一手法载入它的 engine 包。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_NAME = "ovb_client"
_SOURCE = Path(__file__).resolve().parents[1] / "openviking_client.py"


def _load_client_module() -> None:
    if _MODULE_NAME in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)


_load_client_module()
