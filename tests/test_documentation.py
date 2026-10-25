from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_maintained_local_links_resolve_inside_repository(self):
        docs = [ROOT / name for name in ('README.md', 'README.zh-CN.md', 'CONTRIBUTING.md')]
        docs += sorted((ROOT / 'docs').glob('*.md'))
        for path in docs:
            text = path.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('# '), str(path.relative_to(ROOT)))
            for target in re.findall(r'\[[^\]]*\]\(([^\s]+)\)', text):
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.path:
                    continue
                destination = (path.parent / unquote(url.path)).resolve()
                self.assertTrue(destination.is_relative_to(ROOT), target)
                self.assertTrue(destination.exists(), f'{path.relative_to(ROOT)} -> {target}')
