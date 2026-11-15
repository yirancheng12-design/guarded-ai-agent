from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from guarded_agent.page_extraction import extract_main_text
from guarded_agent.work_tools import WorkTools


class PageExtractionTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'files').mkdir()
        self.tools = WorkTools(root / 'files', root / 'data', 'extract-test', lambda *a: True)

    def fetch(self, html, content_type='text/html'):
        with patch('guarded_agent.work_tools.public_get', return_value={
                'url': 'https://example.com/article', 'text': html, 'content_type': content_type}):
            return self.tools.fetch_url('https://example.com/article')

    def test_actual_extractor_removes_navigation(self):
        paragraphs = ''.join('<p>' + text * 12 + '</p>' for text in (
            '这是关于本地助手的正文，介绍网页阅读能力与文档处理方式。',
            '第二段详细说明测试步骤和可验证的结果，便于读者理解。',
            '第三段讨论实际应用场景和功能边界，说明如何使用助手。'))
        html = '<html><body><nav>UNIQUE_NAVIGATION</nav><article>' + paragraphs + '</article><footer>UNIQUE_FOOTER</footer></body></html>'
        result = self.fetch(html)
        self.assertEqual(result['extraction'], 'trafilatura')
        self.assertIn('这是关于本地助手的正文', result['text'])
        self.assertNotIn('UNIQUE_NAVIGATION', result['text'])
        self.assertNotIn('UNIQUE_FOOTER', result['text'])

    def test_empty_or_failed_extraction_falls_back_and_keeps_links(self):
        for value in (None, ''):
            with self.subTest(value=value), patch('guarded_agent.page_extraction.extract_main_text', return_value=value):
                result = self.fetch('<p>Short page</p><script>SECRET_SCRIPT</script><a href="/next">Next</a>')
                self.assertEqual(result['extraction'], 'basic_html')
                self.assertIn('Short page', result['text'])
                self.assertNotIn('SECRET_SCRIPT', result['text'])
                self.assertEqual(result['links'], ['https://example.com/next'])
        with patch('trafilatura.extract', side_effect=ValueError('malformed')):
            self.assertIsNone(extract_main_text('<html>', 'https://example.com'))

    def test_plain_text_is_unchanged_and_output_is_bounded(self):
        self.assertEqual(self.fetch('plain text', 'text/plain')['text'], 'plain text')
        with patch('guarded_agent.page_extraction.extract_main_text', return_value='x' * 20000):
            self.assertEqual(len(self.fetch('<p>text</p>')['text']), 16000)

    def test_review_still_blocks_network(self):
        from guarded_agent.work_tools import Declined
        self.tools.local_context = True
        self.tools.approve = lambda *a: False
        with patch('guarded_agent.work_tools.public_get') as get:
            with self.assertRaises(Declined):
                self.tools.fetch_url('https://example.com')
            get.assert_not_called()
