"""OpenViking 记忆桥：把 N.E.K.O 接到本地 OpenViking 记忆服务。

入口层只做三件事：读配置、调 OpenVikingClient、把结果包成 Ok/Err。
真正的 HTTP 与路径逻辑都在 openviking_client.py（不依赖 SDK，可单测）。
"""

from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from .openviking_client import (
    OpenVikingClient,
    OpenVikingError,
    format_hit,
    is_under,
    normalize_uri,
    safe_name,
    truncate,
)

DEFAULT_BASE_URL = "http://127.0.0.1:1933"
DEFAULT_MEMORY_ROOT = "viking://user/default/memories"
MAX_RECALL_LIMIT = 50
MAX_LS_LIMIT = 200


def _default_config() -> dict[str, Any]:
    """配置默认值。入口在 startup 之前被调用时也有一份可用配置，不会 KeyError。"""
    return {
        "base_url": DEFAULT_BASE_URL,
        "api_key": "",
        "timeout_s": 30.0,
        "memory_root": DEFAULT_MEMORY_ROOT,
        "max_chars": 2000,
        "default_limit": 5,
    }


@neko_plugin
class OpenVikingBridgePlugin(NekoPluginBase):
    """OpenViking 记忆桥。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._client: OpenVikingClient | None = None
        self._cfg: dict[str, Any] = _default_config()

    # --- 配置 ---

    async def _load_config(self) -> None:
        """读一次配置；配置变了就丢掉旧客户端，下次调用重建。"""
        raw = await self.config.dump()
        section = raw.get("openviking", {}) if isinstance(raw, dict) else {}
        if not isinstance(section, dict):
            section = {}
        cfg = _default_config()
        cfg.update(
            {
                "base_url": str(section.get("base_url") or cfg["base_url"]),
                "api_key": str(section.get("api_key") or ""),
                "timeout_s": float(section.get("timeout_s") or cfg["timeout_s"]),
                "memory_root": str(section.get("memory_root") or cfg["memory_root"]),
                "max_chars": int(section.get("max_chars") or cfg["max_chars"]),
                "default_limit": int(section.get("default_limit") or cfg["default_limit"]),
            }
        )
        self._cfg = cfg
        self._client = None

    def _ov(self) -> OpenVikingClient:
        if self._client is None:
            self._client = OpenVikingClient(self._cfg["base_url"], self._cfg["api_key"], self._cfg["timeout_s"])
        return self._client

    # --- 生命周期 ---

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._load_config()
        self.logger.info("openviking_bridge ready (base_url=%s)", self._cfg["base_url"])
        return Ok({"status": "ready", "base_url": self._cfg["base_url"]})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._load_config()
        self.logger.info("openviking_bridge config reloaded")
        return Ok({"status": "reloaded"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._client = None
        return Ok({"status": "stopped"})

    # --- 运行时入口 ---

    @plugin_entry(
        id="ov_health",
        name="服务体检",
        description="检查 OpenViking 服务是否在线，返回版本与认证模式。只读。",
    )
    async def ov_health(self, **_):
        try:
            info = self._ov().health()
        except OpenVikingError as exc:
            return Err(SdkError(str(exc)))
        return Ok(
            {
                "healthy": bool(info.get("healthy", True)),
                "version": str(info.get("version") or ""),
                "auth_mode": str(info.get("auth_mode") or ""),
                "base_url": self._cfg["base_url"],
            }
        )

    @plugin_entry(
        id="ov_recall",
        name="回忆检索",
        description="按语义检索 OpenViking 里的记忆与资料，返回命中的 viking:// 路径与摘要。只读。",
    )
    async def ov_recall(self, query: str, limit: int = 0, target_uri: str = "", **_):
        text = (query or "").strip()
        if not text:
            return Err(SdkError("query is required"))
        size = int(limit) if limit else self._cfg["default_limit"]
        size = max(1, min(size, MAX_RECALL_LIMIT))
        scope = (target_uri or "").strip()
        try:
            if scope:
                scope = normalize_uri(scope)
            hits = self._ov().find(text, limit=size, target_uri=scope)
        except OpenVikingError as exc:
            return Err(SdkError(str(exc)))
        return Ok(
            {
                "query": text,
                "count": len(hits),
                "hits": [format_hit(hit, self._cfg["max_chars"]) for hit in hits],
            }
        )

    @plugin_entry(
        id="ov_read",
        name="读取条目",
        description="读取一个 viking:// 文件的正文，超长按配置截断。只读。",
    )
    async def ov_read(self, uri: str, **_):
        try:
            target = normalize_uri(uri)
            body = self._ov().read(target)
        except OpenVikingError as exc:
            return Err(SdkError(str(exc)))
        return Ok(
            {
                "uri": target,
                "chars": len(body),
                "content": truncate(body, self._cfg["max_chars"]),
            }
        )

    @plugin_entry(
        id="ov_ls",
        name="浏览目录",
        description="列出一个 viking:// 目录下的条目，用来发现可读的路径。只读。",
    )
    async def ov_ls(self, uri: str = "", limit: int = 50, **_):
        size = max(1, min(int(limit), MAX_LS_LIMIT))
        try:
            target = normalize_uri(uri) if (uri or "").strip() else normalize_uri(self._cfg["memory_root"])
            entries = self._ov().ls(target, limit=size)
        except OpenVikingError as exc:
            return Err(SdkError(str(exc)))
        return Ok({"uri": target, "count": len(entries), "entries": entries})

    @plugin_entry(
        id="ov_remember",
        name="写入记忆",
        description="把一条记忆写进 OpenViking，只允许写在配置的 memory_root 目录下。会写数据。",
    )
    async def ov_remember(self, name: str, content: str, **_):
        label = (name or "").strip()
        body = (content or "").strip()
        if not label or not body:
            return Err(SdkError("name and content are required"))
        root = normalize_uri(self._cfg["memory_root"])
        target = normalize_uri(root + "/" + safe_name(label) + ".md")
        if not is_under(root, target):
            return Err(SdkError("target escapes memory_root"))
        try:
            result = self._ov().write(target, body)
        except OpenVikingError as exc:
            return Err(SdkError(str(exc)))
        return Ok(
            {
                "uri": target,
                "written_bytes": int(result.get("written_bytes") or len(body.encode("utf-8"))),
            }
        )

    # --- LLM 工具（对话期调用）---

    @llm_tool(
        name="openviking_recall",
        description="从用户的长期记忆里检索相关记忆与资料。需要回忆过去的偏好、决定、事实时使用。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要回忆的内容，写成一句具体的查询"},
            },
            "required": ["query"],
        },
    )
    async def openviking_recall(self, *, query: str):
        result = await self.ov_recall(query=query)
        if result.is_err():
            return {"output": {"error": str(result.err())}, "is_error": True, "error": "OPENVIKING_UNAVAILABLE"}
        return {"output": result.unwrap()}

    @llm_tool(
        name="openviking_remember",
        description="把一条值得长期记住的信息写进用户的长期记忆。仅在用户明确要求记住时使用。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "这条记忆的短名字，用作文件名"},
                "content": {"type": "string", "description": "要记住的内容"},
            },
            "required": ["name", "content"],
        },
    )
    async def openviking_remember(self, *, name: str, content: str):
        result = await self.ov_remember(name=name, content=content)
        if result.is_err():
            return {"output": {"error": str(result.err())}, "is_error": True, "error": "OPENVIKING_WRITE_FAILED"}
        return {"output": result.unwrap()}
