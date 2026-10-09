"""Existing OpenAI-compatible provider, now with a hard local deadline."""

import json
import os
import subprocess
import sys
import time
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services.generation_protocol import GenerationError, GenerationText
from httpx import Client
from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError, APITimeoutError
from app.services.runtime_metrics import provider_call


@provider_call
def generate_response(
    messages: list[dict], api_key: str, base_url: str, model_name: str, response_format: dict | None = None, timeout_seconds: float = 45
) -> str:
    from app.schemas.llm_settings import validate_provider_url

    base_url = validate_provider_url(base_url)
    timeout_seconds = min(45, max(1, float(timeout_seconds)))
    # Credentials only travel over the private child stdin pipe, never argv/files/logs.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
        }
    }
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            input=json.dumps(
                {
                    "messages": messages,
                    "api_key": api_key,
                    "base_url": base_url,
                    "model_name": model_name,
                    "response_format": response_format,
                    "timeout_seconds": timeout_seconds,
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            raise GenerationError("provider_protocol", "Model worker did not complete")
        output = json.loads(result.stdout)
        if not isinstance(output, dict) or not isinstance(output.get("metadata", {}), dict):
            raise GenerationError("provider_protocol", "Invalid model worker envelope")
        metadata = output.get("metadata", {})
        logging.getLogger(__name__).info("Generation call: protocol=%s finish=%s input_tokens=%s output_tokens=%s elapsed_ms=%s",
            "json_schema" if response_format else "prompt", metadata.get("finish_reason"),
            metadata.get("prompt_tokens"), metadata.get("completion_tokens"), metadata.get("elapsed_ms"))
        if output.get("error"):
            raise GenerationError(output.get("category", "provider_error"), output["error"], metadata)
        if not isinstance(output.get("answer"), str) or not output["answer"].strip():
            raise GenerationError("empty_response")
        return GenerationText(output["answer"], metadata)
    except subprocess.TimeoutExpired:
        raise GenerationError("provider_timeout", f"LLM provider exceeded the {timeout_seconds:g}-second time limit") from None
    except (ValueError, OSError):
        raise GenerationError("provider_protocol", "LLM provider request failed") from None


def request_provider(messages, api_key, base_url, model_name, response_format=None, timeout_seconds=45):
    started = time.perf_counter()
    sdk_timeout = min(30.0, max(0.5, timeout_seconds - 2))
    # Disallow redirects so a provider cannot forward the user's key to another host.
    with OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=sdk_timeout,
        max_retries=0,
        http_client=Client(follow_redirects=False, timeout=sdk_timeout),
    ) as client:
        response = client.chat.completions.create(
            model=model_name, messages=messages, temperature=0.2, max_tokens=2048,
            **({"response_format": response_format} if response_format else {})
        )
        return decode_response(response, started)


def decode_response(response, started):
    metadata = {"elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                "completion_tokens": response.usage.completion_tokens if response.usage else None,
                "total_tokens": response.usage.total_tokens if response.usage else None}
    if not response.choices or len(response.choices) != 1:
        raise GenerationError("provider_protocol", metadata=metadata)
    choice = response.choices[0]
    metadata["finish_reason"] = choice.finish_reason
    if choice.finish_reason == "length":
        raise GenerationError("output_truncated", metadata=metadata)
    if choice.message.refusal or choice.finish_reason == "content_filter":
        raise GenerationError("provider_refusal", metadata=metadata)
    if choice.finish_reason != "stop" or choice.message.tool_calls or choice.message.function_call:
        raise GenerationError("incomplete_response", metadata=metadata)
    answer = choice.message.content
    if not isinstance(answer, str) or not answer.strip():
        raise GenerationError("empty_response", metadata=metadata)
    return GenerationText(answer, metadata)


if __name__ == "__main__":
    try:
        from index_worker import limit_memory

        limit_memory()
        answer = request_provider(**json.loads(sys.stdin.read()))
        print(json.dumps({"answer": str(answer), "metadata": answer.metadata}))
    except GenerationError as error:
        print(json.dumps({"error": "Model response rejected", "category": error.category, "metadata": error.metadata}))
    except APITimeoutError:
        print(json.dumps({"error": "LLM provider timeout", "category": "provider_timeout"}))
    except RateLimitError:
        print(json.dumps({"error": "LLM provider rate limit exceeded", "category": "provider_rate_limit"}))
    except APIConnectionError:
        print(json.dumps({"error": "Failed to connect to LLM provider", "category": "provider_connection"}))
    except APIStatusError as error:
        print(json.dumps({"error": f"LLM provider error: {error.status_code}", "category": "provider_status"}))
    except Exception:
        print(json.dumps({"error": "LLM provider request failed", "category": "provider_error"}))
