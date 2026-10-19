"""Resolve API settings without importing a processing pipeline."""
import os

def _api_settings(settings):
    base_url = (settings.get('base_url') or os.getenv('LLM_BASE_URL', '')).strip()
    api_key = (settings.get('api_key') or os.getenv('LLM_API_KEY', '')).strip()
    model = (settings.get('model') or os.getenv('LLM_MODEL', '')).strip()
    if not base_url or not api_key or not model:
        raise RuntimeError('请填写 API 地址、API Key 和支持图片输入的模型名称，或在 .env 配置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。')
    return base_url, api_key, model
