"""Contract checks; no network, real database or real collection jobs."""
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tracker.web.routes import partner_api as api
from tracker.worker import scheduler
from tracker.db import partner_jobs


class PartnerAPIContract(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.client = TestClient(self.app)

    def test_missing_key_is_not_bypassed(self):
        response = self.client.get('/api/v1/ads')
        self.assertEqual(response.status_code, 401)

    def test_existing_search_does_not_queue_external_request(self):
        self.app.dependency_overrides[api.authenticate] = lambda: 'key-hash'
        result = {'ad_count':1, 'cards':[{'id':'123'}], 'total':1,'page':0,'pages':1,'page_size':20}
        with patch.object(api, 'existing', return_value=result), patch.object(api.db, 'connect', side_effect=AssertionError('should not queue')):
            r=self.client.post('/api/v1/searches',json={'title':'Example Drama'})
        self.assertEqual(r.status_code,202)
        self.assertEqual(r.json()['source'],'l2_library')
        self.assertEqual(r.json()['results']['cards'][0]['id'],'123')

    def test_pagination_cursor_and_invalid_input(self):
        now=datetime(2026,11,5,tzinfo=timezone.utc)
        c=api.encode_cursor(now,42,now)
        self.assertEqual(api.decode_cursor(c),(now,42,now))
        self.app.dependency_overrides[api.authenticate] = lambda: 'key-hash'
        self.assertEqual(self.client.get('/api/v1/ads?cursor=%25%25%25').status_code,400)
        self.assertEqual(self.client.get('/api/v1/ads?updated_since=2026-01-01T00:00:00').status_code,400)

    def test_invalid_search_and_id_are_rejected(self):
        self.app.dependency_overrides[api.authenticate] = lambda: 'key-hash'
        self.assertEqual(self.client.post('/api/v1/searches',json={'title':'...'}).status_code,422)
        self.assertEqual(self.client.get('/api/v1/ads/not-a-number').status_code,404)

    def test_missing_drama_enqueues_and_returns_polling_url(self):
        self.app.dependency_overrides[api.authenticate] = lambda: 'key-hash'
        calls=[]
        class Result:
            def __init__(self,value): self.value=value
            def fetchone(self): return self.value
        class Conn:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,sql,args=()):
                calls.append((sql,args))
                if 'SELECT id FROM partner_search_jobs' in sql:return Result(None)
                if 'count(*) AS n' in sql:return Result({'n':0})
                return Result(None)
        with patch.object(api,'existing',return_value={'ad_count':0}), patch.object(api.db,'connect',return_value=Conn()):
            r=self.client.post('/api/v1/searches',json={'title':'No Existing Drama'})
        self.assertEqual(r.status_code,202)
        self.assertEqual(r.json()['status'],'queued')
        self.assertIn(r.json()['job_id'],r.json()['status_url'])
        inserted=[args for sql,args in calls if 'INSERT INTO partner_search_jobs' in sql][0]
        self.assertEqual(inserted[2:],('No Existing Drama','US'))

    def test_jobs_are_scoped_to_the_api_key(self):
        self.app.dependency_overrides[api.authenticate] = lambda: 'key-hash'
        class Conn:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,sql,args):
                assert 'key_hash=%s' in sql and args[1]=='key-hash'
                return self
            def fetchone(self):return None
        with patch.object(api.db,'connect',return_value=Conn()):
            self.assertEqual(self.client.get('/api/v1/searches/00000000-0000-0000-0000-000000000001').status_code,404)

    def test_partner_worker_uses_per_job_settings_without_mutating_shared_config(self):
        job={'id':'job-1','title':'Target drama','country':'US'}
        progress=scheduler.RunProgress(total=2)
        with patch.object(partner_jobs,'claim',return_value=job), patch.object(partner_jobs,'finish') as finish, \
             patch.object(scheduler,'_run_once',return_value=(42,progress)) as run:
            self.assertTrue(scheduler._run_partner_job())
        override=run.call_args.kwargs['settings_override']
        self.assertEqual(override['collect_keyword'],'Target drama')
        self.assertEqual(override['collect_country'],'US')
        self.assertEqual(override['collect_match_mode'],'exact')
        self.assertEqual(override['collect_launch_days'],0)
        finish.assert_called_once_with('job-1',42,progress)


if __name__ == '__main__':
    unittest.main()
