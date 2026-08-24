---
name: hermes-antigravity-pipeline
description: "Use when setting up Antigravity OMP gateway in Hermes."
version: 1.0.0
author: Andrey
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [hermes, antigravity, omp, gemini, gateway, providers, configuration]
---

# Hermes Antigravity Provider & Pipeline Setup

## When to Use

Use when setting up, configuring, or troubleshooting Google Antigravity (OMP gateway) as a custom model provider in Hermes Agent for reliable and stable operation.

Руководство по подключению и бесперебойной настройке шлюза Google Antigravity (OMP / OpenCode Multi-Provider gateway) в качестве основного или вспомогательного провайдера в Hermes Agent.

---

## 1. Архитектура и требования

- **Шлюз:** OMP Gateway (обычно запущен локально или в Tailscale-сети на порту `4000`, например `http://127.0.0.1:4000/v1` или `http://100.x.y.z:4000/v1`).
- **Протокол:** OpenAI-совместимый (`/v1/chat/completions`, `/v1/models`).
- **Модели:** `google-antigravity/gemini-3.7-flash`, `google-antigravity/gemini-3.6-flash`, `google-antigravity/claude-sonnet-4-5`, `google-antigravity/claude-opus-4-6` и др.

---

## 2. Предварительная проверка шлюза (Curl Probe)

Перед внесением изменений в конфигурацию Hermes обязательно проверьте доступность шлюза и поведение моделей:

```bash
# 1. Проверка списка доступных моделей
curl -s http://127.0.0.1:4000/v1/models | jq .

# 2. Проверочный запрос с указанием reasoning_effort
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google-antigravity/gemini-3.7-flash",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 100,
    "reasoning_effort": "low"
  }' | jq .
```

---

## 3. Настройка `~/.hermes/config.yaml`

Для работы без сбоев конфигурация должна учитывать особенности рассуждающих моделей (Gemini 3.7 / Claude):

### А. Основной блок модели и провайдера

```yaml
model:
  provider: antigravity
  default: google-antigravity/gemini-3.7-flash
  max_output_tokens: 8192

providers:
  antigravity:
    base_url: http://127.0.0.1:4000/v1
    api_mode: chat_completions
    default_model: google-antigravity/gemini-3.7-flash
    max_output_tokens: 8192
```

### Б. Обязательная настройка `reasoning_overrides`

> **Критично:** Модель `gemini-3.7-flash` выбрасывает ошибку `400 "Thinking level MINIMAL is not supported"` при отсутствии явного уровня reasoning или при уровне `minimal`. Уровень `high` может вызывать ошибку `502 "thought-only response without final output"`. Оптимальный стабильный уровень — `low` или `medium`.

```yaml
agent:
  max_turns: 150
  api_max_retries: 5
  reasoning_effort: medium
  reasoning_overrides:
    gemini: medium
    gemini-3.6-flash: medium
    gemini-3.7-flash: medium
    gemini-3.7-flash-tiered: medium
    google-antigravity/gemini-3.6-flash: medium
    google-antigravity/gemini-3.7-flash: medium
    google-antigravity/gemini-3.7-flash-tiered: medium
    google-antigravity/claude-sonnet-4-5: medium
    google-antigravity/claude-opus-4-6: medium
```

### В. Настройка субагентов и вспомогательных сервисов

```yaml
delegation:
  provider: antigravity
  model: google-antigravity/gemini-3.7-flash
  reasoning_effort: medium
  max_concurrent_children: 2

auxiliary:
  vision:
    provider: antigravity
    model: google-antigravity/gemini-3.7-flash
  compression:
    provider: antigravity
    model: google-antigravity/gemini-3.7-flash
  title_generation:
    enabled: false
    provider: antigravity
    model: google-antigravity/gemini-3.7-flash
```

---

## 4. Патчи для исключения сбоев (Stability Fixes)

### 1. `chat_completions.py`: нормализация аргументов субагентов
Субагенты могут передавать имя модели без префикса (`gemini-3.7-flash`) или без `extra_body`. Убедитесь, что в `agent/transports/chat_completions.py` (или методе построения kwargs) проставляется fallback reasoning:
```python
# Если модель относится к gemini или antigravity, гарантировать reasoning_effort:
if "gemini" in model.lower() or "antigravity" in provider_name:
    if not api_kwargs.get("reasoning_effort"):
        api_kwargs["reasoning_effort"] = "medium"  # или "low"
```

### 2. `error_classifier.py`: корректная обработка 429
Текст ошибки `Resource has been exhausted (e.g. check quota)` от OMP является RPS/rate limit, а не исчерпанием баланса (billing). В `error_classifier.py` паттерны `resource has been exhausted` и `check quota` должны классифицироваться как `FailoverReason.rate_limit`, чтобы Hermes делал retry, а не падал с фальшивым сообщением «Billing or credits exhausted».

---

## 5. Проверка работоспособности

```bash
# 1. Проверка через CLI чат
hermes chat -q "Напиши один факт о космосе"

# 2. Перезапуск шлюза Hermes (при запуске как systemd service)
systemctl --user restart hermes-gateway
```

---

## 6. Типичные проблемы и решения

| Симптом | Причина | Решение |
|---|---|---|
| `400 Thinking level MINIMAL is not supported` | OMP шлет default/minimal thinking в Gemini 3.7 | Прописать `reasoning_overrides` со значением `low`/`medium` для всех вариантов имени модели |
| `502 thought-only response without final output` | Модель потратила весь `max_tokens` на reasoning, либо reasoning=high | Увеличить `max_output_tokens` до `8192`+, снизить reasoning до `low` или `medium` |
| `429 RESOURCE_EXHAUSTED` | Превышен лимит запросов в секунду (RPS) | Увеличить `api_max_retries: 5+`, снизить параллельность `max_concurrent_children` до 2 |
| Субагенты падают с `Unknown provider` | Задан старый строковый формат `custom_providers` | Использовать словарь `providers.antigravity` в `config.yaml` |
