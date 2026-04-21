from openai import OpenAI

from app.core.config import settings

def generate_response(message: str, api_key: str) -> str:
    client = OpenAI(
        api_key=api_key,
        base_url=settings.NVIDIA_API_BASE
    )
    response = client.chat.completions.create(
        model=settings.NVIDIA_CHAT_MODEL,
        messages=[
            {"role": "user", "content": message}
        ],
        temperature=0.2,
        max_tokens=512
    )
    return response.choices[0].message.content or ""