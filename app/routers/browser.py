from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import BASE_DIR

router = APIRouter(tags=['browser'])


@router.get('/api/ziniao/launcher')
async def download_ziniao_launcher():
    """Download the Ziniao WebDriver launcher batch file."""
    bat_path = BASE_DIR / 'ziniao_webdriver.bat'
    if not bat_path.exists():
        raise HTTPException(status_code=404, detail='Launcher script not found')
    return FileResponse(
        path=str(bat_path),
        filename='ziniao_webdriver.bat',
        media_type='application/octet-stream',
    )
