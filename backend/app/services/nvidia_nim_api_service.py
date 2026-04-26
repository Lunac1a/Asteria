from openai import OpenAI


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

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "user", "content": message}
        ],
        temperature=0.2,
        max_tokens=512,
    )

    return response.choices[0].message.content or ""
