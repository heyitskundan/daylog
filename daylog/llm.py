"""LLM provider abstraction for the opt-in labeling step.

Two backends, selected by config:
  - "ollama" (default): fully local, no API key, no data leaves the machine.
  - "claude":  Anthropic API (claude-opus-4-8), higher-quality summaries; opt-in.

Both expose one method: complete_json(system, user, schema) -> dict, where `schema` is a
JSON Schema the model's output is constrained to. Labeling never runs automatically; it is
only invoked by the `label` command.
"""

from __future__ import annotations

import json

from .config import Config, anthropic_api_key, openai_api_key


class LLMError(RuntimeError):
    pass


def availability(cfg: Config) -> tuple[bool, str]:
    """Return (ready, reason) for the configured provider without making a call."""
    provider = cfg.ai.provider
    if provider == "ollama":
        try:
            import ollama  # noqa: F401
        except Exception:
            return False, "ollama package not installed (uv sync --extra ai) and/or Ollama not running"
        return True, "ollama"
    if provider == "claude":
        try:
            import anthropic  # noqa: F401
        except Exception:
            return False, "anthropic package not installed (uv sync --extra ai)"
        if not anthropic_api_key():
            return False, "ANTHROPIC_API_KEY not set in environment"
        return True, "claude"
    if provider == "lmstudio":
        base = cfg.ai.lmstudio_base_url
        try:
            import requests

            requests.get(f"{base.rstrip('/')}/models", timeout=3).raise_for_status()
        except Exception:
            return False, f"LM Studio not reachable at {base} (start LM Studio, load a model, and enable the local server)"
        return True, "lmstudio"
    if provider == "openai":
        if not openai_api_key():
            return False, "OPENAI_API_KEY not set in environment"
        return True, "openai"
    return False, f"unknown provider '{provider}'"


def complete_json(cfg: Config, system: str, user: str, schema: dict) -> dict:
    provider = cfg.ai.provider
    if provider == "ollama":
        return _ollama_json(cfg, system, user, schema)
    if provider == "claude":
        return _claude_json(cfg, system, user, schema)
    if provider == "lmstudio":
        return _openai_compatible_json(
            cfg.ai.lmstudio_base_url, cfg.ai.lmstudio_model, None, system, user, schema
        )
    if provider == "openai":
        return _openai_compatible_json(
            cfg.ai.openai_base_url, cfg.ai.openai_model, openai_api_key(), system, user, schema
        )
    raise LLMError(f"unknown AI provider '{provider}'")


def _ollama_json(cfg: Config, system: str, user: str, schema: dict) -> dict:
    import ollama

    try:
        resp = ollama.chat(
            model=cfg.ai.ollama_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format=schema,            # constrain output to the JSON schema
            options={"temperature": 0.2},
        )
    except Exception as exc:  # connection refused, model not pulled, etc.
        raise LLMError(f"Ollama call failed: {exc}") from exc
    content = resp["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Ollama returned non-JSON output: {exc}") from exc


def _resolve_openai_model(base_url: str, model: str, headers: dict) -> str:
    """If no model is configured, ask the server for its loaded/first model."""
    if model:
        return model
    import requests

    try:
        data = requests.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=5).json()
        return data["data"][0]["id"]
    except Exception as exc:
        raise LLMError(
            "No model configured and could not auto-detect one. "
            "Load a model in LM Studio, or set [ai] lmstudio_model in config.toml."
        ) from exc


def _openai_compatible_json(
    base_url: str, model: str, api_key: str | None, system: str, user: str, schema: dict,
    idle_timeout: float = 600.0,
) -> dict:
    """Call any OpenAI-compatible /chat/completions endpoint (LM Studio, OpenAI, vLLM, ...).

    Streams the response so a slow or cold-loading local model doesn't hit a hard total
    timeout: `idle_timeout` is the max gap *between* chunks (covers a cold model load before
    the first token, then steady generation keeps it alive).
    """
    import requests

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    model = _resolve_openai_model(base_url, model, headers)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "stream": True,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "daylog_tasks", "strict": True, "schema": schema},
        },
    }
    chunks: list[str] = []
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload,
            stream=True, timeout=(15, idle_timeout),
        )
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            choice = (obj.get("choices") or [{}])[0]
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                chunks.append(piece)
    except requests.RequestException as exc:
        raise LLMError(f"OpenAI-compatible call to {base_url} failed: {exc}") from exc

    content = "".join(chunks).strip()
    if not content:
        raise LLMError(f"{base_url} returned an empty response (model may have failed to load).")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{base_url} returned non-JSON output: {exc}") from exc


def _claude_json(cfg: Config, system: str, user: str, schema: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    try:
        resp = client.messages.create(
            model=cfg.ai.claude_model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIError as exc:
        raise LLMError(f"Claude call failed: {exc}") from exc
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Claude returned non-JSON output: {exc}") from exc
