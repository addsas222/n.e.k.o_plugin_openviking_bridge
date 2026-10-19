# OpenViking 记忆桥 (openviking_bridge)

把 [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) 接到本地
[OpenViking](https://github.com/volcengine/OpenViking) 记忆服务：让猫娘能**回忆**
（语义检索长期记忆）与**记住**（把一条信息写进记忆库）。

## 它做什么

| 入口 | 作用 | 是否写数据 |
|---|---|---|
| `ov_health` | 服务体检：服务是否在线、版本、认证模式 | 只读 |
| `ov_recall` | 回忆检索：按语义查记忆与资料，返回路径 + 摘要 | 只读 |
| `ov_read` | 读取条目：读一个 `viking://` 文件的正文 | 只读 |
| `ov_ls` | 浏览目录：列目录，用来发现可读路径 | 只读 |
| `ov_remember` | 写入记忆：写一条记忆，**只允许写在 `memory_root` 下** | 写 |

另外注册两个对话期 LLM 工具，模型会在合适的时候自己调用：

- `openviking_recall` —— 需要回忆过去的偏好、决定、事实时。
- `openviking_remember` —— 用户明确要求「记住这个」时。

## 前置条件

1. 本机跑着 OpenViking Server，默认地址 `http://127.0.0.1:1933`。
2. 如果服务端开了认证，把 API Key 填进配置（见下）。

验证服务在不在：

```powershell
curl.exe -s http://127.0.0.1:1933/health
```

## 配置

插件第一次运行时会生成用户配置：

```text
<用户数据根目录>\plugins\openviking_bridge\config\plugin.toml
```

Windows 上通常是：

```text
C:\Users\<你>\AppData\Local\N.E.K.O\plugins\openviking_bridge\config\plugin.toml
```

```toml
[openviking]
base_url = "http://127.0.0.1:1933"   # 服务地址，只接受 http:// 或 https://
api_key = ""                          # 服务端开了认证才需要填
timeout_s = 30.0                      # 单次请求超时（秒）
memory_root = "viking://user/default/memories"  # 写入根目录，越界会被拒绝
max_chars = 2000                      # 返回给面板/模型的正文与摘要上限（字符）
default_limit = 5                     # 检索默认条数
```

**API Key 只放在用户数据目录，不要写进仓库。** 仓库里的 `config.example.toml`
永远是空 Key。

## 安装

在 N.E.K.O. 插件管理器里：左栏「开发模式」→ 开启 →「加载未打包插件」→ 选中本目录
（含 `plugin.toml` 的那一层）→ 校验 → 加载。

因为要注册 LLM 工具，本插件 `auto_start = true`：随宿主启动，对话里的「回忆 / 记住」
才不会漏。

## 安全边界

- `ov_remember` 只能写在 `memory_root` 之下；文件名会被收敛成单个路径片段，
  含 `..` 或路径分隔符的输入会被过滤，越界直接返回错误。
- `base_url` 只接受 `http://` / `https://`，避免 `file://` 之类的协议被当作服务地址。
- API Key 只出现在 HTTP 请求头里，不进日志、不进返回值。
- 错误消息只带状态码与端点路径，不回传服务端原始错误正文以外的隐私内容。

## 开发与自测

```powershell
# 纯逻辑 + 本地假服务回环测试（不需要 N.E.K.O 宿主）
python -m pytest -q

# 风格检查
ruff check --config ruff.toml .
```

`tests/test_client.py` 会起一个本地 HTTP 假服务，验证真实请求路径、`X-API-Key`
请求头与响应信封拆解；`tests/test_manifest.py` 校验 `plugin.toml` 的入口声明与
`@plugin_entry` 装饰器一致。

## 已知边界

- 只覆盖 OpenViking HTTP API 的 5 个端点；sessions / skills / 快照等未接入。
- 写入用 `mode = "replace"`：同名记忆会被覆盖，不做版本历史。
- `ov_ls` 的返回结构按 `simple=true` 的列表形态解析；服务端若改结构需要同步调整。
