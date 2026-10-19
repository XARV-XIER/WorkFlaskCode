# Providers

WorkFlaskCode talks to providers through one internal interface. Each adapter maps its vendor's
request, response, tool-call and error formats into the same types.

| Kind | Endpoint | Notes |
|---|---|---|
| `openrouter` | `https://openrouter.ai/api/v1` | Live catalogue with per-token pricing, tool and vision flags |
| `openai` | `https://api.openai.com/v1` | Chat Completions with function tools |
| `anthropic` | `https://api.anthropic.com/v1` | Native Messages API; tool_use and tool_result blocks; paginated models |
| `openai_compatible` | You set `base_url` | Any server speaking the Chat Completions API. Model list used if it exists |

## Keys

Lookup order: environment variable, OS keyring (if `keyring` is installed), then the credentials
file in the agent home (mode 0600). Keys are never written to `config.toml`, never shown in full,
and redacted from error text.

| Provider | Environment variable |
|---|---|
| OpenRouter | `OPENROUTER_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Custom `name` | `WORKFLASKCODE_KEY_<NAME>` |

## Model discovery

`@models` (or `workflaskcode models`) fetches the provider's list and caches it for 24 hours.
`@models refresh` forces a fetch. Fields the provider does not report are shown as `?`.
A listed model is not a guarantee of access, so `@changemodel` tests the model with a minimal
request before switching.

## Fallback chain

`@fallback add <spec>` adds a model on the default provider. Use `provider::model-id` to use a
different configured provider. A fallback is tried only for model-specific failures: rate limits,
unavailable models, unsupported tool calls, overload and context overflow. Account-wide failures
(invalid key, billing, quota) stop the task with a clear message, because another model will not
fix them.

## Error classes

| Class | Retried | Falls back | Typical cause |
|---|---|---|---|
| `network`, `timeout` | Yes, backoff | No | Connectivity, slow provider |
| `rate_limit`, `overload` | Yes, backoff or Retry-After | Yes | Busy model or provider |
| `auth` | No | No | Wrong or revoked key |
| `billing`, `quota` | No | No | No credit, account limit |
| `unavailable` | No | Yes | Model not found or not offered |
| `unsupported_tools` | No | Yes | Model cannot do tool calls |
| `context_overflow` | No | Yes | Request larger than the model's window |
| `bad_request`, `malformed` | No | No | Invalid request or unexpected response |

## Budgets

`limits.max_estimated_cost_usd` (0 means off) stops new requests once the session's
provider-reported cost reaches the limit. Providers that do not report cost are not counted.
Paid inference is never enabled automatically; a budget only limits spend that you already
authorised by configuring a paid model.

## Adding a provider

Implement `Provider` in `src/workflaskcode/providers/`, add it to `PROVIDER_CLASSES` in
`providers/__init__.py`, add its kind to `PROVIDER_KINDS` in `config.py`, and cover it with
mocked-transport tests that check request shape, response parsing and error classification.
