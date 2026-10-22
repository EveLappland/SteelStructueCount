"""Application-level limits for independent page/view AI requests."""
import os


def view_concurrency(settings=None):
    value=(settings or {}).get('max_concurrency')
    if value is None or value=='':value=os.getenv('LLM_MAX_CONCURRENCY') or '5'
    if isinstance(value,bool) or not str(value).isdigit() or int(value)<1:
        raise ValueError('AI并发数必须是正整数；设为1可串行执行。')
    return int(value)
