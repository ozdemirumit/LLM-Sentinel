# User Guide

This guide is for developers and applications that connect to the LLM Sentinel. You will learn how to authenticate, send chat requests, use model aliases, handle streaming, work with embeddings, and interpret error responses.

---

## Table of Contents

- [Getting Your API Key](#getting-your-api-key)
- [Authentication](#authentication)
- [Chat Completions](#chat-completions)
- [Streaming](#streaming)
- [Embeddings](#embeddings)
- [Model Aliases](#model-aliases)
- [Available Models](#available-models)
- [Tool / Function Calling](#tool--function-calling)
- [Response Headers](#response-headers)
- [Cache Control](#cache-control)
- [Rate Limits and Quotas](#rate-limits-and-quotas)
- [Error Handling](#error-handling)
- [SDK Examples](#sdk-examples)
- [IDE Integration](#ide-integration)
- [FAQ](#faq)

---

## Getting Your API Key

Your admin will create a client for you in the admin panel and provide an API key that looks like:

```
sk-proxy-a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6
```

This key is shown **only once** when created. If you lose it, ask your admin to regenerate it.

---

## Authentication

Include your API key in every request using one of these methods:

**Method 1: X-API-Key header**
```
X-API-Key: sk-proxy-xxx
```

**Method 2: Authorization Bearer header (OpenAI SDK compatible)**
```
Authorization: Bearer sk-proxy-xxx
```

Both methods work identically. Method 2 is compatible with the OpenAI SDK's default behavior.

---

## Chat Completions

The primary endpoint. Fully compatible with the OpenAI Chat Completions API.

**Endpoint:** `POST /v1/chat/completions`

### Basic Request

```json
{
  "model": "smart",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Explain quantum computing in simple terms."}
  ]
}
```

### Full Request (All Options)

```json
{
  "model": "claude-sonnet-4-6",
  "messages": [
    {"role": "system", "content": "You are a coding expert."},
    {"role": "user", "content": "Write a Python function to sort a list."}
  ],
  "temperature": 0.7,
  "max_tokens": 2048,
  "top_p": 0.9,
  "stop": ["\n\n"],
  "stream": false
}
```

### Response Format

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "claude-sonnet-4-6",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Here's a Python sorting function..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 150,
    "total_tokens": 175
  }
}
```

### Finish Reasons

| Value | Meaning |
|-------|---------|
| `stop` | Model finished naturally |
| `length` | Hit max_tokens limit |
| `tool_calls` | Model wants to call a tool/function |

---

## Streaming

For real-time token-by-token output, set `stream: true`.

**Request:**
```json
{
  "model": "fast",
  "messages": [{"role": "user", "content": "Tell me a story."}],
  "stream": true
}
```

**Response:** Server-Sent Events (SSE) stream:
```
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"},"index":0}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","choices":[{"delta":{"content":"Once"},"index":0}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","choices":[{"delta":{"content":" upon"},"index":0}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","choices":[{"delta":{"content":" a"},"index":0}]}

data: [DONE]
```

**Python streaming example:**
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8765/v1", api_key="sk-proxy-xxx")

stream = client.chat.completions.create(
    model="smart",
    messages=[{"role": "user", "content": "Write a poem about coding."}],
    stream=True,
)

for chunk in stream:
    content = chunk.choices[0].delta.content
    if content:
        print(content, end="", flush=True)
print()
```

---

## Embeddings

Create vector embeddings for RAG applications, semantic search, and similarity matching.

**Endpoint:** `POST /v1/embeddings`

**Request:**
```json
{
  "model": "text-embedding-3-small",
  "input": "The quick brown fox jumps over the lazy dog."
}
```

**Multiple inputs:**
```json
{
  "model": "text-embedding-3-small",
  "input": [
    "First document text",
    "Second document text",
    "Third document text"
  ]
}
```

**Response:**
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.0023, -0.0091, 0.0152, ...],
      "index": 0
    }
  ],
  "model": "text-embedding-3-small",
  "usage": {
    "prompt_tokens": 9,
    "total_tokens": 9
  }
}
```

**Supported providers for embeddings:** OpenAI, Azure OpenAI, Gemini, Ollama, and any OpenAI-compatible endpoint.

---

## Model Aliases

Instead of remembering long model names, use short aliases:

| Alias | Provider | Actual Model | Best For |
|-------|----------|-------------|----------|
| `fast` | Anthropic | claude-haiku-4-5-20251001 | Quick tasks, high throughput |
| `smart` | Anthropic | claude-sonnet-4-6 | General purpose, coding |
| `powerful` | Anthropic | claude-opus-4-6 | Complex reasoning, analysis |
| `gpt-fast` | OpenAI | gpt-4o-mini | Quick tasks via OpenAI |
| `gpt-smart` | OpenAI | gpt-4o | General purpose via OpenAI |
| `gemini-fast` | Gemini | gemini-1.5-flash | Quick tasks via Google |
| `gemini-smart` | Gemini | gemini-1.5-pro | Complex tasks via Google |

Your admin may have created additional custom aliases. Use `GET /v1/models` to see all available models and aliases.

**Example — using an alias:**
```python
response = client.chat.completions.create(
    model="fast",  # resolves to claude-haiku automatically
    messages=[{"role": "user", "content": "Summarize this text..."}],
)
```

---

## Available Models

**Endpoint:** `GET /v1/models`

Returns all models from all active providers plus all aliases:

```bash
curl http://localhost:8765/v1/models -H "Authorization: Bearer sk-proxy-xxx"
```

```json
{
  "object": "list",
  "data": [
    {"id": "claude-sonnet-4-6", "object": "model", "owned_by": "anthropic"},
    {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
    {"id": "fast", "object": "model", "owned_by": "alias:anthropic"},
    {"id": "smart", "object": "model", "owned_by": "alias:anthropic"}
  ]
}
```

Models with `owned_by` starting with `alias:` are aliases.

---

## Tool / Function Calling

The proxy supports OpenAI-format tool calling across all providers.

**Request with tools:**
```json
{
  "model": "smart",
  "messages": [{"role": "user", "content": "What's the weather in London?"}],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "City name"}
          },
          "required": ["city"]
        }
      }
    }
  ]
}
```

**Response when model calls a tool:**
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\": \"London\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

The proxy automatically converts tool formats between OpenAI and Anthropic styles.

---

## Response Headers

Every response includes useful headers:

| Header | Value | Meaning |
|--------|-------|---------|
| `X-Request-ID` | `uuid` | Unique ID for this request (use for debugging with admin) |
| `X-Cache-Hit` | `true` / `false` | Whether the response came from cache |
| `X-Context-Truncated` | `true` / `false` | Whether older messages were removed to fit context window |
| `X-Queue-Wait-Ms` | `150` | Milliseconds spent waiting in queue before processing |

**Example — checking headers in Python:**
```python
import httpx

r = httpx.post("http://localhost:8765/v1/chat/completions",
    headers={"Authorization": "Bearer sk-proxy-xxx"},
    json={"model": "fast", "messages": [{"role": "user", "content": "Hi"}]})

print(r.headers["X-Request-ID"])    # e.g. "a1b2c3d4-..."
print(r.headers["X-Cache-Hit"])     # "true" or "false"
```

---

## Cache Control

If caching is enabled by your admin, identical requests may return cached responses instantly.

**Bypass cache for a specific request:**
```
X-Cache-Control: no-cache
```

```python
response = httpx.post(url, headers={
    "Authorization": "Bearer sk-proxy-xxx",
    "X-Cache-Control": "no-cache",   # always call the LLM
}, json=body)
```

**What is cached:**
- Non-streaming requests only
- Keyed by: messages + model + temperature
- Responses within the token limit configured by admin

**What is NOT cached:**
- Streaming responses (`stream: true`)
- Requests with `X-Cache-Control: no-cache`
- Responses exceeding the cache token limit

---

## Rate Limits and Quotas

Your client may have rate limits and quotas configured:

| Limit Type | Description | Header When Exceeded |
|-----------|-------------|---------------------|
| **RPM** (Requests/Minute) | Max requests per minute | `Retry-After: <seconds>` |
| **TPM** (Tokens/Minute) | Max tokens per minute | `Retry-After: <seconds>` |
| **Daily Quota** | Max tokens per day | — |
| **Concurrent** | Max simultaneous requests | — |

When a limit is exceeded, you receive HTTP `429 Too Many Requests`:

```json
{
  "detail": "Client rate limit exceeded"
}
```

**Best practices:**
- Implement exponential backoff on 429 responses
- Respect the `Retry-After` header value
- Use streaming for long responses to reduce perceived latency
- Use `fast` alias for simple tasks to reduce token usage

---

## Error Handling

### Error Response Format

```json
{
  "detail": "Human-readable error message"
}
```

### Error Codes

| Code | Meaning | What To Do |
|------|---------|-----------|
| `400` | Invalid request format | Check your request body and parameters |
| `401` | Invalid or missing API key | Verify your API key is correct |
| `403` | Permission denied or content blocked | Your client lacks permission, or content policy blocked the request |
| `404` | Endpoint not found | Check the URL path |
| `413` | Request body too large | Reduce message count or content length |
| `429` | Rate limit or quota exceeded | Wait and retry with backoff |
| `502` | LLM provider error | The upstream provider returned an error — retry |
| `503` | No healthy providers | All provider keys exhausted — contact admin |

**Python error handling example:**
```python
from openai import OpenAI, APIError, RateLimitError

client = OpenAI(base_url="http://localhost:8765/v1", api_key="sk-proxy-xxx")

try:
    response = client.chat.completions.create(
        model="smart",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print(response.choices[0].message.content)

except RateLimitError as e:
    print(f"Rate limited. Retry after: {e.response.headers.get('Retry-After', '?')}s")

except APIError as e:
    print(f"API error {e.status_code}: {e.message}")
```

---

## SDK Examples

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8765/v1",
    api_key="sk-proxy-xxx",
)

# Simple chat
response = client.chat.completions.create(
    model="smart",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is Python?"},
    ],
    temperature=0.7,
    max_tokens=500,
)
print(response.choices[0].message.content)
```

### Python (httpx — raw HTTP)

```python
import httpx

response = httpx.post(
    "http://localhost:8765/v1/chat/completions",
    headers={"Authorization": "Bearer sk-proxy-xxx"},
    json={
        "model": "fast",
        "messages": [{"role": "user", "content": "Hello!"}],
    },
    timeout=120,
)
data = response.json()
print(data["choices"][0]["message"]["content"])
```

### JavaScript / Node.js (openai SDK)

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8765/v1",
  apiKey: "sk-proxy-xxx",
});

const response = await client.chat.completions.create({
  model: "smart",
  messages: [{ role: "user", content: "Hello!" }],
});
console.log(response.choices[0].message.content);
```

### JavaScript (fetch — raw HTTP)

```javascript
const response = await fetch("http://localhost:8765/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer sk-proxy-xxx",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "fast",
    messages: [{ role: "user", content: "Hello!" }],
  }),
});
const data = await response.json();
console.log(data.choices[0].message.content);
```

### curl

```bash
# Simple request
curl http://localhost:8765/v1/chat/completions \
  -H "Authorization: Bearer sk-proxy-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"fast","messages":[{"role":"user","content":"Hello!"}]}'

# Streaming
curl http://localhost:8765/v1/chat/completions \
  -H "Authorization: Bearer sk-proxy-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"Tell me a joke."}],"stream":true}'

# List models
curl http://localhost:8765/v1/models \
  -H "Authorization: Bearer sk-proxy-xxx"
```

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8765/v1",
    api_key="sk-proxy-xxx",
    model="smart",
)

response = llm.invoke("Explain machine learning.")
print(response.content)
```

### LlamaIndex

```python
from llama_index.llms.openai import OpenAI

llm = OpenAI(
    api_base="http://localhost:8765/v1",
    api_key="sk-proxy-xxx",
    model="smart",
)

response = llm.complete("What is RAG?")
print(response.text)
```

---

## IDE Integration

### Cursor

1. Open Settings (Ctrl+,) > Models
2. Set **OpenAI API Base:** `http://localhost:8765/v1`
3. Set **API Key:** `sk-proxy-xxx`
4. Set **Model:** `smart` (or any alias/model name)
5. All AI features now route through the proxy

### Continue (VS Code extension)

Edit `~/.continue/config.json`:
```json
{
  "models": [
    {
      "title": "LLM Sentinel",
      "provider": "openai",
      "model": "smart",
      "apiBase": "http://localhost:8765/v1",
      "apiKey": "sk-proxy-xxx"
    }
  ]
}
```

### Cline (VS Code extension)

In Cline settings:
- **API Provider:** OpenAI Compatible
- **Base URL:** `http://localhost:8765/v1`
- **API Key:** `sk-proxy-xxx`
- **Model:** `smart`

---

## FAQ

**Q: Can I use any model name?**
A: Yes. Use an alias (like `fast`), a real model name (like `claude-sonnet-4-6`), or any model from `GET /v1/models`. The proxy auto-detects the provider from the model name.

**Q: What happens if my messages are too long?**
A: The proxy automatically truncates older messages (keeping the system prompt) to fit within the model's context window. You'll see `X-Context-Truncated: true` in the response headers.

**Q: Is my data filtered before reaching the LLM?**
A: Yes. The proxy automatically masks sensitive data (passwords, API keys, credit cards, etc.) using 20+ built-in regex patterns before sending to the provider.

**Q: What if the provider is down?**
A: The proxy automatically retries with different API keys and falls back to other providers in the configured fallback chain. You may see slightly higher latency but requests still succeed.

**Q: Can I use this with async Python?**
A: Yes. The OpenAI SDK supports async:
```python
from openai import AsyncOpenAI
client = AsyncOpenAI(base_url="http://localhost:8765/v1", api_key="sk-proxy-xxx")
response = await client.chat.completions.create(model="fast", messages=[...])
```
