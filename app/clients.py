import httpx
from openai import OpenAI


def get_client(api_key, base_url):
    if not api_key:
        return None
    kwargs = {
        "api_key": api_key,
        "http_client": httpx.Client(trust_env=False),
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)
