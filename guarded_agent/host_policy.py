"""Read the local operator's policy; web requests and model calls cannot set it."""
from pathlib import Path
import json
from .runtime_options import validated_options


def read_policy(directory):
    path = Path(directory) / '.guarded-policy' / '.env'
    if not path.exists(): return None
    if path.stat().st_size > 16000: raise ValueError('本机执行配置过大')
    data = json.loads(path.read_text(encoding='utf-8'))
    required = {'auto_execute', 'full_access', 'auto_context', 'allow_web', 'projects_root', 'runtime_options'}
    if not isinstance(data, dict) or set(data) != required: raise ValueError('本机执行配置字段无效')
    for key in ('auto_execute', 'full_access', 'auto_context', 'allow_web'):
        if type(data[key]) is not bool: raise ValueError('本机执行开关需要布尔值')
    if not isinstance(data['projects_root'], str) or not Path(data['projects_root']).is_absolute():
        raise ValueError('本地项目目录需要完整路径')
    validated_options(data['runtime_options'])
    return data
