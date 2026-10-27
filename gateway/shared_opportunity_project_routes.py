"""Explicit project downloads and metadata over existing human process roles."""
from fastapi import Depends,HTTPException,Path,Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from shared.shared_opportunity_project import ExportRequest,RegenerateRequest,Remove
from .shared_opportunity_projects import SharedOpportunityProjects
from .process_collaboration import ProcessCollaborationError


def add_routes(router,store,human,body,response):
    projects=SharedOpportunityProjects(store);base='/v1/human/processes/{workflow_id}/opportunity-projects';ID=r'^[A-Za-z0-9_-]{1,80}$';PROJECT=r'^project_[a-f0-9]{64}$'

    def archive(operation,*args):
        try:content,record=operation(*args)
        except ProcessCollaborationError as exc:raise HTTPException(exc.status,str(exc)) from None
        return Response(content,media_type='application/zip',headers={'Cache-Control':'no-store',
            'Content-Disposition':'attachment; filename="owg-shared-opportunity-project.zip"',
            'X-OWG-Project-Id':record['manifest']['project_id'],'X-OWG-Archive-SHA256':record['archive_sha256'],'X-OWG-Project-Record-SHA256':record['content_sha256']})

    @router.get('/shared-opportunity-project-client.js',include_in_schema=False)
    def script():
        from pathlib import Path as FilePath
        return Response(FilePath(__file__).with_name('shared_opportunity_project_client.js').read_text(encoding='utf-8'),media_type='application/javascript',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @router.get(base)
    def detail(workflow_id:str=Path(pattern=ID),p=Depends(human)):
        return response(projects.detail,p,workflow_id)

    @router.post(base)
    async def generate(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,projects.opportunities.preflight,p,workflow_id)
        parsed=await body(request,ExportRequest,maximum=4096)
        return await run_in_threadpool(archive,projects.generate,p,workflow_id,parsed)

    @router.get(base+'/{project_id}/manifest')
    def manifest(workflow_id:str=Path(pattern=ID),project_id:str=Path(pattern=PROJECT),p=Depends(human)):
        result=response(projects.detail,p,workflow_id,exporting_project=project_id)
        result.headers['Content-Disposition']='attachment; filename="owg-shared-opportunity-project-manifest.json"';return result

    @router.post(base+'/{project_id}/regenerate')
    async def regenerate(request:Request,workflow_id:str=Path(pattern=ID),project_id:str=Path(pattern=PROJECT),p=Depends(human)):
        await run_in_threadpool(response,projects.opportunities.preflight,p,workflow_id)
        parsed=await body(request,RegenerateRequest,maximum=4096)
        return await run_in_threadpool(archive,projects.regenerate,p,workflow_id,project_id,parsed)

    @router.post(base+'/delete')
    async def delete(request:Request,workflow_id:str=Path(pattern=ID),p=Depends(human)):
        await run_in_threadpool(response,projects.opportunities.preflight,p,workflow_id,deleting=True)
        return await run_in_threadpool(response,projects.delete,p,workflow_id,await body(request,Remove,maximum=4096))
