"""xAI transport. The OpenAI-compatible SDK is a client, not the provider."""
from openai import AsyncOpenAI

GROK_MODEL = 'grok-4.7'
XAI_BASE_URL = 'https://api.x.ai/v1'


def grok_client(api_key, *, base_url=None):
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError('XAI_API_KEY is required for Grok 4.7.')
    # Fail closed rather than sending an xAI key to a legacy provider override.
    if base_url not in (None, XAI_BASE_URL):
        raise ValueError('This starter uses the xAI API directly.')
    return AsyncOpenAI(api_key=api_key, base_url=XAI_BASE_URL, max_retries=1)
