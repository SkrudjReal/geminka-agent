# open-antigravity

> 将 [Antigravity](https://antigravity.dev) 暴露为 **OpenAI** 和 **Anthropic** 兼容的 API 服务。通过 `base_url + key` 即可让任何兼容客户端（Claude Code、Cursor、Continue、Python SDK 等）调用 Antigravity。

## 快速开始

```bash
# 前提：Antigravity 桌面应用已运行，至少打开一个 workspace
npm install
npm run dev
```

服务默认运行在 `http://localhost:4000`。

## 使用方式

### OpenAI 格式

```bash
# base_url = http://localhost:4000/v1
# api_key 可以填任意值

curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-key" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Streaming
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-key" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "stream": true,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Anthropic 格式

```bash
# base_url = http://localhost:4000
# x-api-key 可以填任意值

curl http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: any-key" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4000/v1", api_key="any")
resp = client.chat.completions.create(
    model="claude-sonnet-4-20250514",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

```python
import anthropic

client = anthropic.Anthropic(base_url="http://localhost:4000", api_key="any")
msg = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(msg.content[0].text)
```

## 可选 Header

| Header | 说明 |
|--------|------|
| `x-workspace` | 指定 workspace，例如 `file:///Users/you/project`。不传则使用第一个可用 workspace |
| `x-conversation-id` | 复用已有对话（跳过创建新对话） |

## 可用模型

| 外部名称 | Antigravity 内部 ID | 显示名称 |
|----------|---------------------|----------|
| `gemini-3.1-pro` | MODEL_PLACEHOLDER_M37 | Gemini 3.1 Pro (High) |
| `gemini-3.1-pro-low` | MODEL_PLACEHOLDER_M36 | Gemini 3.1 Pro (Low) |
| `gemini-3-flash` | MODEL_PLACEHOLDER_M47 | Gemini 3 Flash |
| `claude-sonnet-4-20250514` | MODEL_PLACEHOLDER_M35 | Claude Sonnet 4.6 (Thinking) |
| `claude-opus-4-20250514` | MODEL_PLACEHOLDER_M26 | Claude Opus 4.6 (Thinking) |
| `gpt-oss-120b` | MODEL_OPENAI_GPT_OSS_120B_MEDIUM | GPT-OSS 120B |

也可直接传入内部 ID（如 `MODEL_PLACEHOLDER_M35`）。

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions |
| `POST` | `/v1/messages` | Anthropic Messages |
| `GET` | `/v1/models` | 可用模型列表 |
| `GET` | `/health` | 健康检查 |

## 架构

```
客户端 (OpenAI SDK / Anthropic SDK / cURL)
    │
    ▼ HTTP :4000
open-antigravity (纯 Node.js, 零依赖)
    │
    │  routes/openai.ts   → /v1/chat/completions
    │  routes/anthropic.ts → /v1/messages
    │
    │  converter.ts → 创建对话 + 发消息 + 等待回复
    │
    │  bridge/
    │  ├── discovery.ts  → ps + lsof 发现 language_server
    │  ├── statedb.ts    → SQLite 读取 API key
    │  └── grpc.ts       → gRPC-Web 通信
    │
    ▼ gRPC-Web (HTTPS, 127.0.0.1)
Antigravity language_server
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `4000` | 服务端口 |
| `HOST` | `0.0.0.0` | 监听地址 |

## 技术栈

- **TypeScript** + **tsx**（开发/运行）
- **零运行时依赖**：纯 Node.js `http` + `https` + `crypto` 模块
- 从 [Antigravity-Mobility-CLI](https://github.com/pikapikaspeedup/Antigravity-Mobility-CLI) 移植 bridge 层

## 免责声明

本项目是一个出于学习和互操作性目的构建的非官方开源工具。运行必须依赖用户本机已安装并合法认证的 Antigravity 桌面应用。与 Google DeepMind 没有任何关联。

## License

MIT
