import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'skills/meow-notify/scripts'
sys.path.insert(0, str(SCRIPTS))
import manage
import reliable_delivery as delivery
import semantic_notify as summary
import notify


class ConfigTests(unittest.TestCase):
    def test_multiline_root_notify_preserves_other_tables(self):
        raw = '# comment\nnotify = [\n "old", # keep previous command\n "argument",\n]\nmodel="chosen"\n[other]\nnotify=["nested"]\n'
        updated = manage.edit_notify(raw, ['new'])
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed['notify'], ['new'])
        self.assertEqual(parsed['other']['notify'], ['nested'])
        self.assertEqual(parsed['model'], 'chosen')

    def test_install_reinstall_uninstall(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'CODEX_HOME': directory, 'MEOW_ENDPOINT':'https://api.chuckfang.com/test-only'}), patch.object(manage, 'spawn_worker'):
            config = Path(directory)/'config.toml'
            config.write_text('notify=["previous"]\nmodel="existing"\n', encoding='utf-8')
            args = type('Args', (), dict(mode='status', endpoint_env='MEOW_ENDPOINT'))()
            with contextlib.redirect_stdout(io.StringIO()):
                manage.install(args)
                manage.install(args)
                manage.uninstall()
            self.assertEqual(tomllib.loads(config.read_text()), {'notify':['previous'], 'model':'existing'})
            runtime = Path(directory)/'notifications/meow-notify-skill'
            self.assertTrue(list((runtime/'backups').glob('*.bak')))

    def test_uninstall_refuses_modified_hook(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'CODEX_HOME': directory}):
            root = Path(directory)
            runtime = root/'notifications/meow-notify-skill'
            runtime.mkdir(parents=True)
            (runtime/'settings.json').write_text('{"previous_notify": null}')
            (root/'config.toml').write_text('notify=["another"]')
            with self.assertRaises(ValueError):
                manage.uninstall()

    def test_endpoint_validation(self):
        for endpoint in ('http://api.chuckfang.com/user', 'https://evil.invalid/user', 'https://api.chuckfang.com/', 'https://user@api.chuckfang.com/x'):
            with self.assertRaises(ValueError):
                manage.validate_endpoint(endpoint)


class DeliveryTests(unittest.TestCase):
    def test_deduplication_and_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = delivery.enqueue({'msg':'本轮回复已生成。'}, 'event', root)
            self.assertEqual(path, delivery.enqueue({'msg':'replacement'}, 'event', root))
            sender = lambda payload: {'accepted': True, 'http_status':200, 'application_status':200}
            self.assertTrue(delivery.process_job(path, sender=sender, root=root))
            self.assertFalse(delivery.process_job(path, sender=sender, root=root))
            self.assertEqual(delivery.read(path)['payload']['msg'], '本轮回复已生成。')
            self.assertNotIn('本轮', (root/'delivery-log.jsonl').read_text())

    def test_bounded_failure_and_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = delivery.enqueue({'msg':'测试'}, 'failure', root)
            sender = lambda payload: {'accepted':False, 'http_status':503}
            delivery.process_job(path, sender=sender, now=100, root=root)
            self.assertFalse(delivery.process_job(path, sender=sender, now=101, root=root))
            for _ in range(delivery.MAX_ATTEMPTS-1):
                delivery.process_job(path, sender=sender, now=delivery.read(path)['next_attempt'], root=root)
            self.assertEqual(delivery.read(path)['status'], 'failed')
            self.assertFalse(delivery.process_job(path, sender=sender, now=1e12, root=root))

    def test_application_rejection_and_redirection(self):
        class Response:
            status = 200
            def __enter__(self): return io.BytesIO(b'{"status": 403}')
            def __exit__(self, *args): pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'settings.json').write_text('{"endpoint":"https://api.chuckfang.com/test-only"}')
            # Fake response with the same context-manager interface.
            fake = io.BytesIO(b'{"status":403}')
            fake.status = 200
            with patch('urllib.request.OpenerDirector.open', return_value=fake):
                result = delivery.post({'msg':'测试'}, root)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['application_status'], 403)
            self.assertIsNone(delivery.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.invalid'))


class SummaryTests(unittest.TestCase):
    def test_redaction_and_validation(self):
        raw = '邮箱 person@example.invalid 路径 C:/private/report.txt 密钥 ghp_example 链接 https://example.invalid/private ```secret```'
        value = summary.scrub(raw)
        for secret in ('person@', 'C:/private', 'ghp_', 'https://', 'secret'):
            self.assertNotIn(secret, value)
        self.assertEqual(summary.clean_summary('已精简重复表达，保留原有事实'), '已精简重复表达，保留原有事实。')
        for unsafe in ('已完成 https://example.invalid', '已处理账号 user_name', '已完成百分之90的工作'):
            with self.assertRaises(ValueError): summary.clean_summary(unsafe)

    def test_failure_falls_back_without_raw_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = {'thread-id':'test','turn-id':'one','input-messages':['PRIVATE_INPUT'], 'last-assistant-message':'PRIVATE_REPLY'}
            with patch.object(summary, 'ROOT', root), patch.object(delivery, 'ROOT', root), patch.object(summary,'generate',side_effect=RuntimeError('private error content')), patch.object(delivery,'start_worker'):
                summary.run(event)
                summary.run(event)
            jobs = list((root/'queue').glob('*.json'))
            self.assertEqual(len(jobs), 1)
            self.assertIn('摘要暂不可用', delivery.read(jobs[0])['payload']['msg'])
            for path in root.rglob('*.json'):
                value = path.read_text(encoding='utf-8')
                self.assertNotIn('PRIVATE_', value)
                self.assertNotIn('private error content', value)

    def test_recursion_guard(self):
        with patch.dict(os.environ, {'MEOW_SUMMARY_WORKER':'1'}), patch('subprocess.Popen') as launch:
            self.assertEqual(notify.main(), 0)
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
