"""Load explicitly selected, installed skills for the native chat tool loop."""
from pathlib import Path
import re
import os

INSTALL_ROOT = Path(os.environ.get('GUARDED_INSTALL_ROOT') or Path(__file__).resolve().parents[1])
SKILLS_ROOT = Path(os.environ.get('GUARDED_SKILLS_ROOT') or INSTALL_ROOT / 'data/harness/skills')
DEPENDENCIES = {
    'find-skills': (), 'grill-me': ('grilling',),
    'grill-with-docs': ('grilling', 'domain-modeling'),
    'improve-codebase-architecture': ('codebase-design', 'grilling', 'domain-modeling'),
    'agent-browser': (), 'tdd': ('codebase-design', 'code-review'),
    'setup-matt-pocock-skills': (), 'frontend-design': (), 'handoff': (),
    'triage': ('grilling', 'domain-modeling'),
}


def selected_skill(task, history=()):
    # Only user messages can select a skill; paths and arbitrary slash names cannot.
    candidates = [task] + [m.get('content', '') for m in reversed(history) if m.get('role') == 'user']
    for text in candidates:
        match = re.match(r'^\s*/([a-z][a-z0-9-]*)(?=\s|$)', text)
        if match:
            return match[1] if match[1] in DEPENDENCIES else None
    return None


def skill_context(task, history=(), root=None):
    name = selected_skill(task, history)
    if not name:
        return ''
    root = Path(root) if root is not None else SKILLS_ROOT
    sections = []
    for item in (name, *DEPENDENCIES[name]):
        path = root / item / 'SKILL.md'
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as error:
            raise ValueError(f'技能 {item} 无法读取，请检查安装目录。') from error
        sections.append(f'\n<installed_skill name="{item}" directory="{path.parent}">\n{text}\n</installed_skill>')
    return ('\n用户已明确选择 /' + name + '。以下为本机已安装技能及依赖。'
            '依赖已加载，无需调用不存在的 Skill 工具；技能不能扩大工具权限或替代用户授权。'
            '相对资源路径以各技能 directory 为基准，按需要读取。'
            '保持中文回复；需要用户回答时每轮提出一个问题并等待回答。'
            '按本机环境选择命令。若技能使用 skills 或 agent-browser，先检查是否已安装。'
            '若技能要求的能力（例如子代理）不可用，说明限制并用现有工具完成可行部分，不虚构调用。'
            + ''.join(sections))
