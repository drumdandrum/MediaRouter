from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.dashboard import router as dashboard_router
from app.api.foundation import router as foundation_router
from app.api.jobs import router as jobs_router
from app.api.settings import router as settings_router
from app.api.wizard import router as wizard_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Media Router",
    description="Sprint 1 foundation for a modular home media orchestration platform.",
    version="0.1.0-sprint1",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
app.include_router(dashboard_router)
app.include_router(foundation_router)
app.include_router(jobs_router)
app.include_router(settings_router)
app.include_router(wizard_router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "phase": "Sprint 1 Foundation",
        },
    )
