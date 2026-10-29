"""Read-only human-scoped process revision lineage; no admin/service fallback."""
from fastapi import Depends, Path

from .process_lineage import ProcessLineage


def add_routes(router, store, human, body, response):
    lineage = ProcessLineage(store)

    @router.get('/v1/human/processes/{workflow_id}/lineage')
    def detail(workflow_id: str = Path(pattern=r'^[A-Za-z0-9_-]{1,80}$'),
               principal=Depends(human)):
        return response(lineage.detail, principal, workflow_id)
