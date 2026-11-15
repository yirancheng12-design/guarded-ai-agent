"""Compact older tool evidence without corrupting JSON or losing edit tokens."""
import json


def compact_result(content, limit=1000):
    if limit < 128:
        raise ValueError('Evidence excerpt limit must be at least 128')
    if len(content) <= limit:
        return content
    marker = '\n[内容已缩短，需要时重新读取]\n'
    def excerpt(text, size):
        if len(text) <= size: return text
        available = max(0, size-len(marker))
        head = available//2
        return text[:head] + marker + (text[-(available-head):] if available-head else '')
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return excerpt(content, limit)
    critical = ('outcome', 'execution_status', 'reason', 'error', 'exit_code',
                'current', 'objective', 'plan', 'step_count',
                'path', 'sha256', 'edit_version', 'snapshot_id', 'tool', 'arguments', 'result',
                'next_read', 'truncated', 'start_line', 'line_count', 'char_offset', 'max_chars')
    def trim(item, size, count, depth=0):
        if depth > 10: return '[嵌套内容已省略，需要时重新读取]'
        if isinstance(item, dict):
            keys = sorted(item, key=lambda k: k not in critical)
            kept = keys[:max(count, sum(k in critical for k in keys))]
            result = {k: trim(item[k], size, count, depth+1) for k in kept}
            if len(kept) < len(keys): result['_omitted_keys'] = len(keys)-len(kept)
            return result
        if isinstance(item, list):
            return [trim(v, size, count, depth+1) for v in item[:count]] + (
                [{'omitted_items': len(item)-count}] if len(item)>count else [])
        return excerpt(item, size) if isinstance(item, str) else item
    size, count = 600, 30
    while True:
        result = trim(value, size, count)
        if isinstance(result, dict): result['_context_compacted'] = True
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if len(encoded) <= limit: return encoded
        if size > 80: size = max(80, size//2)
        elif count > 1: count = max(1, count//2)
        else:
            return json.dumps({'_context_compacted': True,
                'note': '结果过大，完整记录已保存；请按路径和行范围重新读取。'}, ensure_ascii=False)
