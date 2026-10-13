import tempfile
import unittest
from pathlib import Path

from app.audit import AuditLog


class AuditTest(unittest.TestCase):
    def test_nested_keys_errors_and_prompt_files_are_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=AuditLog(Path(tmp),stage=1,secrets=('secret-key-123',))
            log.event('MODEL_REQUEST_FAILED',api_key='other-secret',nested={'authorization':'Bearer hidden'},
                      error='secret-key-123 Bearer abcdef ?api_key=url-secret&x=1')
            log.text(Path(tmp)/'prompt.txt','credential secret-key-123')
            content=log.path.read_text(encoding='utf-8')+(Path(tmp)/'prompt.txt').read_text(encoding='utf-8')
            for secret in ('secret-key-123','other-secret','hidden','abcdef','url-secret'):
                self.assertNotIn(secret,content)
            self.assertIn('[REDACTED]',content)
            row=log.read()['items'][0]
            self.assertEqual(row['event'],'MODEL_REQUEST_FAILED')
            self.assertEqual(row['stage'],1)

    def test_log_cursor_keeps_events_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=AuditLog(tmp)
            for n in range(3):log.event('STEP',number=n)
            first=log.read(0,2)
            second=log.read(first['next_cursor'],2)
            self.assertEqual(first['total'],3)
            self.assertEqual([r['number'] for r in first['items']+second['items']],[0,1,2])
