"""Read-only analysis comparison endpoints with current per-view membership checks."""
from fastapi import APIRouter, Depends, Path, Query
from starlette.concurrency import run_in_threadpool

from .analysis_publication_routes import ID, response
from .analysis_revision_comparisons import compare


def build_router(store, human):
    router = APIRouter()

    @router.get("/v1/human/analyses/{view_id}/compare")
    async def revision_comparison(
        view_id: str = Path(pattern=ID),
        left: int = Query(ge=1, le=10),
        right: int = Query(ge=1, le=10),
        principal=Depends(human),
    ):
        return await run_in_threadpool(
            response, compare, store, principal, view_id, left, right
        )

    return router
