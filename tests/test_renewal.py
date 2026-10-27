"""离线回归测试：不登录 HidenCloud，不创建账单，不发送通知。"""
import ast
from datetime import datetime
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock


# 仅加载待测试函数，避免模块级凭证读取、网络依赖或浏览器启动。
source = Path(__file__).resolve().parents[1] / 'app.py'
names = {'dismiss_consent_banner', 'click_with_consent', 'due_date_extended', 'write_run_summary'}
tree = ast.parse(source.read_text(encoding='utf-8'))
scope = dict(datetime=datetime, os=os, Path=Path, re=re, log=lambda message: None)
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names],
                        type_ignores=[]), str(source), 'exec'), scope)


class RenewalTests(unittest.TestCase):
    def test_later_date(self):
        self.assertTrue(scope['due_date_extended']('28 Oct 2026', '4 Nov 2026'))

    def test_same_or_earlier_date_is_failure(self):
        for date in ('28 Oct 2026', '27 Oct 2026'):
            self.assertFalse(scope['due_date_extended']('28 Oct 2026', date))

    def test_unknown_dates_are_not_success(self):
        for before, after in [('未知', '4 Nov 2026'), ('28 Oct 2026', '未知'), ('28 Oct 2026', None)]:
            self.assertFalse(scope['due_date_extended'](before, after))

    def test_year_boundary(self):
        self.assertTrue(scope['due_date_extended']('31 Dec 2026', '7 Jan 2027'))

    def test_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / 'summary.md')
            from unittest.mock import patch
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': target}):
                scope['write_run_summary']('OK', '28 Oct 2026', '4 Nov 2026')
            self.assertIn('| 28 Oct 2026 | 4 Nov 2026 |', Path(target).read_text())

    def test_unrelated_click_error_is_not_retried(self):
        page = Mock()
        page.locator.return_value.is_visible.return_value = False
        button = Mock()
        button.click.side_effect = RuntimeError('network error')
        with self.assertRaisesRegex(RuntimeError, 'network error'):
            scope['click_with_consent'](page, button)
        self.assertEqual(button.click.call_count, 1)

    def test_late_ncmp_overlay_retries_once(self):
        page = Mock()
        page.locator.return_value.is_visible.return_value = False
        button = Mock()
        button.click.side_effect = [RuntimeError('ncmp__banner-inner intercepts pointer events'), None]
        scope['click_with_consent'](page, button)
        self.assertEqual(button.click.call_count, 2)


try:
    from patchright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright, '浏览器回归测试在安装 Chrome 的 Actions 环境执行')
class ConsentBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.directory = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.directory.name)

    def tearDown(self):
        self.page.close()
        os.chdir(self.old_cwd)
        self.directory.cleanup()

    def fixture(self, controls):
        self.page.set_content('''<button id="invoice" onclick="this.textContent='Created'">Create Invoice</button>
            <div id="ncmp__tool" style="position:fixed;inset:0;background:white;z-index:9999">
            <div class="ncmp__banner-inner">Privacy choices''' + controls + '</div></div>')

    def test_reject_overlay_then_click_invoice(self):
        self.fixture('''<button onclick="document.querySelector('#ncmp__tool').remove()">Reject All</button>''')
        scope['click_with_consent'](self.page, self.page.locator('#invoice'))
        self.assertEqual(self.page.locator('#invoice').inner_text(), 'Created')

    def test_manage_then_reject(self):
        self.fixture('''<button onclick="this.nextElementSibling.hidden=false;this.remove()">Manage options</button>
            <button hidden onclick="document.querySelector('#ncmp__tool').remove()">Reject All</button>''')
        self.assertTrue(scope['dismiss_consent_banner'](self.page))
        self.assertEqual(self.page.locator('#ncmp__tool').count(), 0)

    def test_no_banner(self):
        self.page.set_content('<button>Create Invoice</button>')
        self.assertTrue(scope['dismiss_consent_banner'](self.page))

    def test_unknown_banner_stops_without_accepting(self):
        self.fixture('''<button onclick="document.querySelector('#ncmp__tool').remove()">Accept All</button>''')
        with self.assertRaisesRegex(RuntimeError, 'ncmp'):
            scope['click_with_consent'](self.page, self.page.locator('#invoice'))
        self.assertTrue(self.page.locator('#ncmp__tool').is_visible())
        self.assertEqual(self.page.locator('#invoice').inner_text(), 'Create Invoice')


if __name__ == '__main__':
    unittest.main()
