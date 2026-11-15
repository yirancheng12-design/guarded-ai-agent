"""Progressive tool descriptions; execution permissions stay with the host."""
import re
from .work_tools import schema

GROUPS = {
    'planning': {'update_plan'},
    'documents': {'read_document', 'create_document', 'describe_image'},
    'desktop': {'list_apps', 'register_app', 'open_app', 'list_windows', 'inspect_window',
                'ui_action', 'inspect_screen', 'desktop_snapshot', 'desktop_action', 'verify_app'},
    'browser': {'browser_open', 'browser_read', 'browser_action', 'preview_file'},
    'web': {'search_web', 'fetch_url'},
    'github': {'github_prepare', 'github_status', 'github_run_tests', 'github_publish'},
}
HINTS = {
    'planning': r'计划|分步|多步骤|复杂|\b(?:plan|steps|complex)\b',
    'documents': r'文档|表格|演示|图片|照片|截图|\b(?:pdf|docx?|xlsx?|pptx?|image|photo)\b',
    'desktop': r'桌面|窗口|控件|点击|鼠标|键盘|打开.{0,8}(?:应用|软件|微信|记事本|计算器)|\b(?:desktop|window|uia)\b',
    'browser': r'浏览器|网页|网站|页面|预览|前端|\b(?:html|browser|playwright|website|frontend)\b',
    'web': r'搜索.{0,12}(?:网|资料|新闻)|联网|查阅|查找.{0,8}资料|最新|https?://|\b(?:web|online|search|fetch)\b',
    'github': r'github|git\b|仓库|拉取请求|提交|分支|\b(?:repo|repository|copilot|pull request)\b',
}


class ToolRouter:
    def __init__(self, specs):
        self.specs = specs
        names = {s['function']['name'] for s in specs}
        self.groups = {k: v & names for k, v in GROUPS.items() if v & names}
        self.active = set()
        self.loader = schema('load_tool_groups',
            'Load additional tool descriptions when needed. Groups: planning (multi-step plan), documents (Office/PDF/images), '
            'desktop (apps/windows/mouse/keyboard), browser (interactive pages/local HTML preview), '
            'web (public search/fetch), github (repository/tests/PR). Loading grants no permissions.',
            {'groups': {'type': 'array', 'items': {'type': 'string', 'enum': list(self.groups)},
                        'minItems': 1, 'maxItems': len(self.groups)}}, ['groups'])

    def load(self, groups):
        if (not isinstance(groups, list) or not groups or len(groups) > len(self.groups)
                or any(not isinstance(g, str) or g not in self.groups for g in groups)):
            raise ValueError('需要选择已提供的工具分组')
        self.active.update(groups)
        return {'outcome': 'success', 'groups': sorted(self.active),
                'tools': sorted(set().union(*(self.groups[g] for g in self.active))),
                'note': '下一轮提供完整参数说明；执行权限和核验规则保持不变。'}

    def select(self, task):
        self.active.update(k for k in self.groups if re.search(HINTS[k], task, re.I))
        if len(task) > 200 and 'planning' in self.groups: self.active.add('planning')
        optional = set().union(*self.groups.values()) if self.groups else set()
        enabled = set().union(*(self.groups[g] for g in self.active)) if self.active else set()
        return [s for s in self.specs if s['function']['name'] not in optional or
                s['function']['name'] in enabled] + [self.loader]
