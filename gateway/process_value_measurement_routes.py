"""Human-only CRUD for reviewed aggregate before/after process comparisons."""
from fastapi import Depends, Path, Query, Request
from starlette.concurrency import run_in_threadpool

from shared.process_value_measurement import Remove, Save
from .process_value_measurements import ProcessValueMeasurements


def add_routes(router, store, human, body, response):
    values = ProcessValueMeasurements(store)
    base = "/v1/human/processes/{workflow_id}/value-measurements"
    ID = r"^[A-Za-z0-9_-]{1,80}$"

    @router.get(base)
    def detail(workflow_id: str = Path(pattern=ID),
               revision: int | None = Query(default=None, ge=1, le=20), p=Depends(human)):
        return response(values.detail, p, workflow_id, revision)

    @router.post(base)
    async def save(request: Request, workflow_id: str = Path(pattern=ID), p=Depends(human)):
        # Refuse an unentitled caller before parsing their aggregate payload.
        await run_in_threadpool(response, values.catalog.preflight, p, workflow_id, writing=True)
        parsed = await body(request, Save, maximum=16 * 1024)
        return await run_in_threadpool(response, values.save, p, workflow_id, parsed)

    @router.get(base + "/export")
    def export(workflow_id: str = Path(pattern=ID),
               revision: int = Query(ge=1, le=20), p=Depends(human)):
        result = response(values.detail, p, workflow_id, revision, exporting=True)
        result.headers["Content-Disposition"] = 'attachment; filename="owg-process-value-measurement.json"'
        return result

    @router.post(base + "/delete")
    async def delete(request: Request, workflow_id: str = Path(pattern=ID), p=Depends(human)):
        await run_in_threadpool(response, values.catalog.preflight, p, workflow_id, writing=True, owner=True)
        parsed = await body(request, Remove, maximum=4096)
        return await run_in_threadpool(response, values.delete, p, workflow_id, parsed)
