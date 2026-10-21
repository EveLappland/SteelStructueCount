import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import create_app
from app.ai_concurrency import view_concurrency
from test_workflow import pdf_bytes


class ConcurrencyFormTests(unittest.TestCase):
    def test_blank_upload_setting_uses_default_and_invalid_limits_create_no_job(self):
        with tempfile.TemporaryDirectory() as temp,TestClient(create_app(Path(temp),start_workers=False)) as client:
            for value in ('','1','8','20','300'):
                response=client.post('/api/jobs',files=[('files',('drawing.pdf',pdf_bytes(),'application/pdf'))],
                                     data={'max_concurrency':value})
                self.assertEqual(response.status_code,200,response.text)
            before=len(client.get('/api/jobs').json())
            for value in ('0','-1','3.5'):
                response=client.post('/api/jobs',files=[('files',('drawing.pdf',pdf_bytes(),'application/pdf'))],
                                     data={'max_concurrency':value})
                self.assertEqual(response.status_code,422,response.text)
            self.assertEqual(len(client.get('/api/jobs').json()),before)
