import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.engine import ConfigurableRagEngine
from app.knowledge.service import KnowledgeService
from app.knowledge.workbench import WorkbenchService
from app.storage.knowledge_store import KnowledgeStore
from app.tasks.service import BackgroundTasks


class TaskApiTests(unittest.TestCase):
    def test_lifespan_worker_paging_and_upload_process_contract(self):
        from app.api import routes_workbench,routes_tasks
        store=KnowledgeStore(Path(tempfile.mkdtemp(prefix='p2-api-')));service=KnowledgeService(ConfigurableRagEngine(store))
        bench=WorkbenchService(service);worker=BackgroundTasks(store)
        app=FastAPI();app.include_router(routes_workbench.router);app.include_router(routes_tasks.router)
        with patch.object(routes_workbench,'service',service),patch.object(routes_workbench,'workbench',bench),patch.object(routes_tasks,'worker',worker):
            with TestClient(app) as client:
                self.assertTrue(client.get('/api/v2/workbench/tasks').json()['worker']['running'])
                doc=client.post('/api/v2/workbench/documents/upload?filename=queue.md&knowledge_base_id=manufacturing',content=b'SYNTHETIC-QUEUE independent process fixture').json()['document']
                task=client.post('/api/v2/workbench/tasks',json={'kind':'process','runtime_mode':'offline','document_ids':[doc['document_id']],'process_options':{'parser_mode':'native'}})
                self.assertEqual(task.status_code,202);identity=task.json()['task_id']
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    saved=client.get('/api/v2/workbench/tasks/'+identity).json()
                    if saved['status']=='completed':break
                    time.sleep(.02)
                self.assertEqual(saved['status'],'completed',saved)
                result=client.get('/api/v2/workbench/chunks?page=1&page_size=1').json()
                self.assertEqual(result['total'],1);self.assertEqual(len(result['items']),1)
                self.assertEqual(client.get('/api/v2/workbench/documents?page=1&compact=true').json()['items'][0]['document_id'],doc['document_id'])
                self.assertEqual(client.get('/api/v2/workbench/documents?page_size=1000').status_code,422)
                self.assertEqual(client.get('/api/v2/workbench/tasks?page=0').status_code,422)
                self.assertEqual(client.get('/api/v2/workbench/chunks/'+result['items'][0]['chunk_id']).status_code,200)
                self.assertEqual(client.get('/api/v2/workbench/documents/'+doc['document_id']).status_code,200)
                self.assertEqual(client.get('/api/v2/workbench/documents').json().keys(),{'items'})
            self.assertFalse(worker.health()['running'])
