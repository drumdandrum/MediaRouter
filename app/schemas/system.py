from pydantic import BaseModel


class SystemInfo(BaseModel):
    app_name: str
    app_version: str
    environment_mode: str
    git_branch: str
    git_commit: str
    database_status: str
    database_label: str
    data_path: str
    docker_status: str
    docker_label: str
    container_status: str
    container_label: str
