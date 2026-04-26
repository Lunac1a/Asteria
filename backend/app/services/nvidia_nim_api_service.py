from openai import OpenAI
from openai import APIConnectionError, APIStatusError, RateLimitError


def generate_response(
    message: str,
    api_key: str,
    base_url: str,
    model_name: str,
) -> str:
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "user", "content": message}
            ],
            temperature=0.2,
            max_tokens=16384,
        )

        return response.choices[0].message.content or ""

    except RateLimitError:
        raise RuntimeError("LLM provider rate limit exceeded")

    except APIConnectionError:
        raise RuntimeError("Failed to connect to LLM provider")

    except APIStatusError as e:
        raise RuntimeError(f"LLM provider error: {e.status_code}")

    except Exception:
        raise RuntimeError("Unexpected LLM provider error")
