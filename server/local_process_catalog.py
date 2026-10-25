"""One local owner boundary for the existing reviewed knowledge catalog."""
from shared.workflow_knowledge import KnowledgeStore
from shared.process_catalog import ProcessCatalog
from . import db


class _LocalKnowledgeDB:
    connect = staticmethod(db.connect)

    @staticmethod
    def _execute(conn, sql, params=()):
        return conn.execute(sql, params)


knowledge = KnowledgeStore(_LocalKnowledgeDB())
catalog = ProcessCatalog(knowledge, 'local')
