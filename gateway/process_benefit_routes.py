"""Scoped process-portal seam; no evidence/service-token fallback."""
from fastapi import Depends, Path, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from shared.process_benefits import Save, Remove, PortfolioReview
from .process_benefits import ProcessBenefits


def add_routes(router,store,human,body,response):
    plans=ProcessBenefits(store);base='/v1/human/processes/{workflow_id}/benefits'
    ID=r'^[A-Za-z0-9_-]{1,80}$'

    @router.get('/process-benefit-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('process_benefit_client.js').read_text(encoding='utf-8'),media_type='application/javascript',
            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/v1/human/process-benefit-portfolio')
    def portfolio(currency:str=Query(default='USD',pattern='^(USD|EUR|GBP|SEK)$'),p=Depends(human)):
        return response(plans.portfolio,p,currency)

    @router.post('/v1/human/process-benefit-portfolio/estimate')
    async def estimate(request:Request,p=Depends(human)):
        if 'org:process:read' not in p.scopes:
            from fastapi import HTTPException
            raise HTTPException(403,'Missing process permission: org:process:read')
        data=await body(request,PortfolioReview,maximum=4096)
        return await run_in_threadpool(response,plans.portfolio,p,data.currency,data)

    @router.post('/v1/human/process-benefit-portfolio/export')
    async def export_portfolio(request:Request,p=Depends(human)):
        if 'org:process:read' not in p.scopes:
            from fastapi import HTTPException
            raise HTTPException(403,'Missing process permission: org:process:read')
        data=await body(request,PortfolioReview,maximum=4096)
        result=await run_in_threadpool(response,plans.portfolio,p,data.currency,data,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-modeled-benefit-portfolio.json"'
        return result

    @router.get(base)
    def detail(workflow_id:str=Path(pattern=ID),revision:int|None=Query(default=None,ge=1,le=20),p=Depends(human)):
        return response(plans.detail,p,workflow_id,revision)

    @router.post(base)
    async def save(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,plans.catalog.preflight,p,workflow_id,writing=True)
        return await run_in_threadpool(response,plans.save,p,workflow_id,await body(request,Save,maximum=64*1024))

    @router.get(base+'/export')
    def export(workflow_id:str=Path(pattern=ID),revision:int=Query(ge=1,le=20),p=Depends(human)):
        result=response(plans.detail,p,workflow_id,revision,exporting=True)
        result.headers['Content-Disposition']='attachment; filename="owg-process-benefit-plan.json"'
        return result

    @router.post(base+'/delete')
    async def delete(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,plans.catalog.preflight,p,workflow_id,writing=True,owner=True)
        return await run_in_threadpool(response,plans.delete,p,workflow_id,await body(request,Remove,maximum=4096))
