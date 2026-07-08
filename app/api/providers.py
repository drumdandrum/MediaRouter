from fastapi import APIRouter, HTTPException

from app.schemas.providers import AccountCreate, AccountRead, AccountUpdate, ConnectionTestResult, ProviderCreate, ProviderRead, ProviderUpdate
from app.services.providers import (
    create_account,
    create_provider,
    delete_account,
    delete_provider,
    get_account,
    get_provider,
    list_accounts,
    list_providers,
    test_account,
    update_account,
    update_provider,
)

router = APIRouter(tags=["providers"])


@router.get("/api/providers", response_model=list[ProviderRead])
def read_providers() -> list[ProviderRead]:
    return list_providers()


@router.post("/api/providers", response_model=ProviderRead, status_code=201)
def add_provider(payload: ProviderCreate) -> ProviderRead:
    return create_provider(payload)


@router.get("/api/providers/{provider_id}", response_model=ProviderRead)
def read_provider(provider_id: str) -> ProviderRead:
    provider = get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@router.put("/api/providers/{provider_id}", response_model=ProviderRead)
def edit_provider(provider_id: str, payload: ProviderUpdate) -> ProviderRead:
    provider = update_provider(provider_id, payload)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@router.delete("/api/providers/{provider_id}", status_code=204)
def remove_provider(provider_id: str) -> None:
    if not delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="Provider not found")


@router.get("/api/accounts", response_model=list[AccountRead])
def read_accounts() -> list[AccountRead]:
    return list_accounts()


@router.post("/api/accounts", response_model=AccountRead, status_code=201)
def add_account(payload: AccountCreate) -> AccountRead:
    if not payload.provider_id:
        raise HTTPException(status_code=400, detail="Choose a provider before saving the account.")
    if not payload.friendly_name.strip():
        raise HTTPException(status_code=400, detail="Account name is required.")
    account = create_account(payload)
    if account is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return account


@router.get("/api/accounts/{account_id}", response_model=AccountRead)
def read_account(account_id: str) -> AccountRead:
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.put("/api/accounts/{account_id}", response_model=AccountRead)
def edit_account(account_id: str, payload: AccountUpdate) -> AccountRead:
    if payload.provider_id == "":
        raise HTTPException(status_code=400, detail="Choose a provider before saving the account.")
    if payload.friendly_name is not None and not payload.friendly_name.strip():
        raise HTTPException(status_code=400, detail="Account name is required.")
    account = update_account(account_id, payload)
    if account is None:
        raise HTTPException(status_code=404, detail="Account or provider not found")
    return account


@router.delete("/api/accounts/{account_id}", status_code=204)
def remove_account(account_id: str) -> None:
    if not delete_account(account_id):
        raise HTTPException(status_code=404, detail="Account not found")


@router.post("/api/accounts/{account_id}/test", response_model=ConnectionTestResult)
def test_connection(account_id: str) -> ConnectionTestResult:
    result = test_account(account_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return result
