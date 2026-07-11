from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.broker import router as broker_router
from app.api.dashboard import router as dashboard_router
from app.api.catalog import router as catalog_router
from app.api.foundation import router as foundation_router
from app.api.jobs import router as jobs_router
from app.api.logs import router as logs_router
from app.api.outputs import router as outputs_router
from app.api.providers import router as providers_router
from app.api.runtime import router as runtime_router
from app.api.settings import router as settings_router
from app.api.sources import router as sources_router
from app.api.system import router as system_router
from app.api.wizard import router as wizard_router
from app.core.config import get_settings
from app.main_meta import APP_VERSION

settings = get_settings()

app = FastAPI(
    title="Media Router",
    description="Sprint 7 Live TV M3U output generator for a modular home media orchestration platform.",
    version=APP_VERSION,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
app.include_router(broker_router)
app.include_router(catalog_router)
app.include_router(dashboard_router)
app.include_router(foundation_router)
app.include_router(jobs_router)
app.include_router(logs_router)
app.include_router(outputs_router)
app.include_router(providers_router)
app.include_router(runtime_router)
app.include_router(settings_router)
app.include_router(sources_router)
app.include_router(system_router)
app.include_router(wizard_router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "app_version": APP_VERSION,
        },
    )
