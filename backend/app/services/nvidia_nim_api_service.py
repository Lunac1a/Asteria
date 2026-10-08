"""Existing OpenAI-compatible provider, now with a hard local deadline."""

import json
import os
import subprocess
import sys
from pathlib import Path
from httpx import Client
from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError


def generate_response(
    messages: list[dict], api_key: str, base_url: str, model_name: str
) -> str:
    from app.schemas.llm_settings import validate_provider_url

    base_url = validate_provider_url(base_url)
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
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=45,
        )
        output = json.loads(result.stdout)
        if not output.get("answer"):
            raise RuntimeError(
                output.get("error", "LLM provider returned an empty answer")
            )
        return output["answer"]
    except subprocess.TimeoutExpired:
        raise RuntimeError("LLM provider exceeded the 45-second time limit")
    except (ValueError, OSError):
        raise RuntimeError("LLM provider request failed")


def request_provider(messages, api_key, base_url, model_name):
    # Disallow redirects so a provider cannot forward the user's key to another host.
    with OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=30.0,
        max_retries=0,
        http_client=Client(follow_redirects=False, timeout=30.0),
    ) as client:
        response = client.chat.completions.create(
            model=model_name, messages=messages, temperature=0.2, max_tokens=2048
        )
        answer = response.choices[0].message.content
        if not answer or not answer.strip():
            raise RuntimeError("LLM provider returned an empty answer")
        return answer


if __name__ == "__main__":
    try:
        from index_worker import limit_memory

        limit_memory()
        print(json.dumps({"answer": request_provider(**json.loads(sys.stdin.read()))}))
    except RateLimitError:
        print(json.dumps({"error": "LLM provider rate limit exceeded"}))
    except APIConnectionError:
        print(json.dumps({"error": "Failed to connect to LLM provider"}))
    except APIStatusError as error:
        print(json.dumps({"error": f"LLM provider error: {error.status_code}"}))
    except Exception:
        print(json.dumps({"error": "LLM provider request failed"}))
