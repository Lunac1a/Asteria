"""Actual SQL retrieval entry point; synthetic embeddings isolate filtering behavior."""
import math
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import test_auth  # existing isolated test settings and model registration
from app.db.base import Base
from app.models.knowledge import Workspace, Document, Chunk
from app.services.knowledge_service import retrieve, model_id
from app.core.config import settings


class RetrievalIntegrationTests(unittest.TestCase):
    def test_rescue_preserves_workspace_status_model_and_source_identity(self):
        engine=create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        question='What happens when h(n) is zero everywhere?'
        with Session(engine) as db:
            for key in ['owned','other']:
                db.add(Workspace(id=key,user_id='00000000-0000-0000-0000-000000000001',name=key))
            # SQLite UUID expects UUID objects, while these retrieval-only rows do
            # not need a user session. Use the ORM-compatible representation.
            import uuid
            for row in db.new: row.user_id=uuid.UUID(str(row.user_id))
            db.flush()
            for key,workspace,status,model in [('ready','owned','ready',model_id()),('foreign','other','ready',model_id()),
                                               ('failed','owned','failed',model_id()),('old','owned','ready','old-model')]:
                db.add(Document(id=key,workspace_id=workspace,name='Reference.pdf',storage_key='synthetic',size_bytes=1,
                                status=status,embedding_model=model,chunk_count=1))
                db.flush()
                db.add(Chunk(id=key,document_id=key,ordinal=0,page=2,
                             content='If h(n) is zero everywhere, f(n) equals g(n).',vector=[.32,math.sqrt(1-.32**2)]))
            db.commit()
            with patch('app.services.knowledge_service.run_worker',return_value={'vectors':[[1,0]]}),patch.object(settings,'RETRIEVAL_MIN_SCORE',.35):
                with patch.object(settings,'RETRIEVAL_LEXICAL_RESCUE',False):self.assertEqual(retrieve(db,'owned',question),[])
                with patch.object(settings,'RETRIEVAL_LEXICAL_RESCUE',True):
                    sources=retrieve(db,'owned',question)
                    self.assertEqual(len(sources),1)
                    self.assertEqual(sources[0],{'number':1,'chunk_id':'ready','document_id':'ready','document_name':'Reference.pdf',
                                                  'page':2,'content':'If h(n) is zero everywhere, f(n) equals g(n).'})
