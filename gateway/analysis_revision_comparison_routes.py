"""Read-only analysis comparison endpoints with current per-view membership checks."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from starlette.concurrency import run_in_threadpool

from .analysis_publication_routes import ID, body, response
from .analysis_portfolio import PortfolioRequest, review
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

    @router.post("/v1/human/analysis-portfolio/compare")
    async def portfolio_comparison(request: Request, principal=Depends(human)):
        if 'org:analysis:read' not in principal.scopes:
            raise HTTPException(403, 'Missing analysis permission: org:analysis:read')
        parsed = await body(request, PortfolioRequest, max_bytes=8*1024)
        return await run_in_threadpool(response, review, store, principal, parsed)

    return router
