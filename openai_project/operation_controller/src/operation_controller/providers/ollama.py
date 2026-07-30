"""OllamaToolLoopProvider — thin HTTP wrapper satisfying ToolLoopProvider.

Talks directly to Ollama's OpenAI-compatible /v1/chat/completions endpoint.
Used by LLMPlanningAdapter and LLMExecutionAdapter for real Qwen inference.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

from operation_controller.tool_loop import ProviderResponse


class OllamaToolLoopProvider:
    """ToolLoopProvider backed by a local Ollama instance.

    Reads configuration from environment with sensible defaults:
    - AUDISOR_BASE_URL (default: http://127.0.0.1:11434)
    - AUDISOR_MODEL_ID (default: qwen2.5-coder:7b)
    """

    def __init__(
        self,
        base_url: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("AUDISOR_BASE_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.model_id = (
            model_id
            or os.environ.get("AUDISOR_MODEL_ID", "qwen2.5-coder:7b")
        )

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ProviderResponse:
        """Call the Ollama OpenAI-compatible chat completions endpoint."""
        effective_model = model or self.model_id

        body: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }

        # Only include tools if non-empty (some models reject empty tools array)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=body,
                timeout=timeout_seconds,
            )
        except requests.Timeout:
            return ProviderResponse(
                text="",
                tool_calls=[],
                usage={},
                model=effective_model,
                finish_reason="error",
            )
        except requests.ConnectionError:
            return ProviderResponse(
                text="[provider_error: Ollama unavailable]",
                tool_calls=[],
                usage={},
                model=effective_model,
                finish_reason="error",
            )

        if response.status_code != 200:
            return ProviderResponse(
                text=f"[provider_error: HTTP {response.status_code}]",
                tool_calls=[],
                usage={},
                model=effective_model,
                finish_reason="error",
            )

        return self._parse_response(response.json(), effective_model)

    def _parse_response(self, payload: dict[str, Any], model: str) -> ProviderResponse:
        """Parse OpenAI-compatible chat completion response.

        Handles both structured tool_calls and text-based tool calls
        (Ollama with quantized models sometimes outputs tool calls as JSON text).
        """
        try:
            choice = payload["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            return ProviderResponse(
                text="[provider_error: malformed response]",
                tool_calls=[],
                usage={},
                model=model,
                finish_reason="error",
            )

        finish_reason = choice.get("finish_reason", "stop") or "stop"

        # Extract text content
        text = message.get("content") or ""

        # Extract tool calls if present in structured format
        tool_calls: list[dict[str, Any]] = []
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls and isinstance(raw_tool_calls, list):
            for i, tc in enumerate(raw_tool_calls):
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function", {})
                tool_calls.append({
                    "id": tc.get("id", f"call_{i}"),
                    "type": "function",
                    "function": {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "{}"),
                    },
                })

        # Fallback: detect tool calls in text content (quantized model workaround)
        if not tool_calls and text.strip():
            parsed_calls = self._try_parse_text_tool_calls(text)
            if parsed_calls:
                tool_calls = parsed_calls
                text = ""  # Clear text since it was a tool call
                finish_reason = "tool_calls"

        # Extract usage
        usage: dict[str, int] = {}
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                val = raw_usage.get(key)
                if isinstance(val, (int, float)):
                    usage[key] = int(val)

        return ProviderResponse(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=finish_reason,
        )

    def _try_parse_text_tool_calls(self, text: str) -> list[dict[str, Any]] | None:
        """Attempt to parse JSON tool calls from model text output.

        Some quantized models output tool calls as JSON text rather than
        using the structured tool_calls API. This detects patterns like:
          {"name": "file_read", "arguments": {"path": "..."}}
        Also handles markdown code fences wrapping the JSON.
        """
        import re
        stripped = text.strip()

        # Strip markdown code fences if present
        fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", stripped, re.DOTALL)
        if fence_match:
            stripped = fence_match.group(1).strip()

        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return None

        # Single tool call: {"name": "...", "arguments": {...}}
        if isinstance(parsed, dict) and "name" in parsed and "arguments" in parsed:
            args = parsed["arguments"]
            args_str = json.dumps(args) if isinstance(args, dict) else str(args)
            return [{
                "id": f"call_text_0",
                "type": "function",
                "function": {
                    "name": parsed["name"],
                    "arguments": args_str,
                },
            }]

        # Array of tool calls: [{"name": "...", "arguments": {...}}, ...]
        if isinstance(parsed, list):
            calls = []
            for i, item in enumerate(parsed):
                if isinstance(item, dict) and "name" in item and "arguments" in item:
                    args = item["arguments"]
                    args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                    calls.append({
                        "id": f"call_text_{i}",
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": args_str,
                        },
                    })
            return calls if calls else None

        return None
