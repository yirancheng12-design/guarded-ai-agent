"""Compact older tool evidence without corrupting JSON or losing edit tokens."""
import json


def compact_result(content, limit=1000):
    if len(content) <= limit:
        return content
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return content[:limit] + '\n[较早结果已缩短，需要时重新读取]'
    def trim(item, depth=0):
        if isinstance(item, dict):
            return {key: trim(val, depth+1) for key, val in item.items()}
        if isinstance(item, list):
            return [trim(val, depth+1) for val in item[:30]] + ([{'omitted_items': len(item)-30}] if len(item)>30 else [])
        if isinstance(item, str) and len(item)>600:
            return item[:300] + '\n[内容已缩短，需要时重新读取]\n' + item[-250:]
        return item
    result = trim(value)
    if isinstance(result, dict): result['_context_compacted'] = True
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'))
