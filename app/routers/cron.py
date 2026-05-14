from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.models.user import User
from app.scheduler.cron import (
    add_cron_job,
    list_cron_jobs,
    pause_cron_job,
    remove_cron_job,
    resume_cron_job,
)

router = APIRouter(prefix='/api/cron', tags=['cron'])


class CronJobCreate(BaseModel):
    job_id: str
    task_title: str
    cron_expression: str  # "minute hour day month day_of_week"
    store_id: str | None = None


@router.get('/jobs')
async def list_jobs(_user: User = Depends(get_current_user)):
    return list_cron_jobs()


@router.post('/jobs')
async def create_job(
    body: CronJobCreate, _user: User = Depends(get_current_user)
):
    try:
        return add_cron_job(
            body.job_id, body.task_title, body.cron_expression, body.store_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete('/jobs/{job_id}')
async def delete_job(job_id: str, _user: User = Depends(get_current_user)):
    if remove_cron_job(job_id):
        return {'ok': True}
    raise HTTPException(status_code=404, detail='Job not found')


@router.post('/jobs/{job_id}/pause')
async def pause_job(job_id: str, _user: User = Depends(get_current_user)):
    if pause_cron_job(job_id):
        return {'ok': True}
    raise HTTPException(status_code=404, detail='Job not found')


@router.post('/jobs/{job_id}/resume')
async def resume_job(job_id: str, _user: User = Depends(get_current_user)):
    if resume_cron_job(job_id):
        return {'ok': True}
    raise HTTPException(status_code=404, detail='Job not found')
