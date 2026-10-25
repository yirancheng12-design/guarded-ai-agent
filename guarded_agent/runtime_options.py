"""Validated host-owned tuning; model tool arguments cannot change these limits."""
import math

# None means no count limit; it is serialized as JSON null.
LIMITS = {
    'temperature': (float, 0, 2, .1), 'top_p': (float, .05, 1, .9),
    'num_ctx': (int, 2048, 131072, 16384), 'num_predict': (int, 128, 32768, 4096),
    'max_rounds': (int, 1, 10**12, None),
    'max_tool_calls': (int, 1, 10**12, None),
    'task_timeout': (int, 30, 10**12, 1800), 
    'command_timeout': (int, 1, 10**12, 90),
}


def validated_options(values):
    if not isinstance(values, dict) or set(values) - LIMITS.keys():
        raise ValueError('运行参数字段无效')
    result = {}
    for key, (kind, low, high, default) in LIMITS.items():
        value = values.get(key, default)
        if value is None and key in {'max_rounds', 'max_tool_calls'}:
            result[key] = None
            continue
        types = (int,) if kind is int else (int, float)
        if type(value) not in types or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} 需要在 {low} 到 {high} 之间')
        result[key] = value
    if result['num_predict'] > result['num_ctx']:
        raise ValueError('输出长度不能大于上下文长度')
    return result


def apply_options(toolbox, values):
    options = validated_options(values)
    toolbox.model_options = {key: options[key] for key in ('temperature', 'top_p', 'num_ctx', 'num_predict')}
    for key in ('max_rounds', 'max_tool_calls', 'task_timeout', 'command_timeout'):
        setattr(toolbox, key, options[key])
