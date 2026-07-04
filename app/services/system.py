import os
import subprocess

from app.main_meta import APP_VERSION
from app.core.config import get_settings
from app.schemas.system import SystemInfo
from app.services.settings import get_app_settings


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "Unavailable"
    if result.returncode != 0:
        return "Unavailable"
    return result.stdout.strip() or "Unavailable"


def get_system_info() -> SystemInfo:
    settings = get_app_settings()
    runtime_settings = get_settings()
    in_container = os.path.exists("/.dockerenv")
    app_version = os.getenv("MEDIA_ROUTER_APP_VERSION", APP_VERSION)
    git_branch = os.getenv("MEDIA_ROUTER_GIT_BRANCH") or _run(["git", "branch", "--show-current"])
    git_commit = os.getenv("MEDIA_ROUTER_GIT_COMMIT") or _run(["git", "rev-parse", "--short", "HEAD"])
    docker_version = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    docker_detected = in_container or docker_version != "Unavailable"
    database_ready = runtime_settings.data_dir.exists()
    return SystemInfo(
        app_name=settings.app_name,
        app_version=app_version,
        environment_mode="docker" if in_container else settings.environment_mode,
        git_branch=git_branch,
        git_commit=git_commit,
        database_status="ready" if database_ready else "needs_setup",
        database_label="Ready" if database_ready else "Needs setup",
        data_path=str(runtime_settings.data_dir),
        docker_status="ready" if docker_detected else "not_configured",
        docker_label="Detected" if docker_detected else "Unavailable",
        container_status="ready" if in_container else "not_configured",
        container_label="Container" if in_container else "Local process",
    )
