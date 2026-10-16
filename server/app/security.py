"""Authentication and authorization dependencies for the API and MCP gateway."""

from dataclasses import dataclass
import datetime
import hashlib
import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Agent, ApiKey
from app.tenancy import DEFAULT_TENANT_ID, bind_tenant, get_principal_resolvers, get_token_extractors, validate_tenant_id


@dataclass(frozen=True)
class Principal:
    """An authenticated caller. ``is_admin`` means administrator of its own tenant only."""

    tenant_id: str
    is_admin: bool
    scope: str = ""
    agent_email: str | None = None
    principal_id: str = ""

    def __post_init__(self):
        validate_tenant_id(self.tenant_id)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Missing or invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Principal:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        for extractor in get_token_extractors():
            token = (extractor(request) or "").strip()
            if token:
                break
        else:
            raise _unauthorized()

    return await authenticate_token(token, db)


async def authenticate_token(token: str, db: AsyncSession) -> Principal:
    """Resolve a credential to a principal and bind ``db`` to the principal's tenant."""
    principal = await _resolve_principal(token, db)
    bind_tenant(db, principal.tenant_id)
    return principal


async def _resolve_principal(token: str, db: AsyncSession) -> Principal:
    admin_token = settings.ADMIN_API_KEY.strip()
    if admin_token and hmac.compare_digest(token, admin_token):
        token_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        return Principal(tenant_id=DEFAULT_TENANT_ID, is_admin=True, principal_id=f"admin:{token_id}")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == token_hash))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        # Credentials understood by a hosted layer (accounts, sessions). A resolver may raise to reject.
        for resolver in get_principal_resolvers():
            resolved = await resolver(token, db)
            if resolved is not None:
                return resolved
    if (
        not api_key
        or (api_key.expires_at is not None and api_key.expires_at <= datetime.datetime.utcnow())
        or (api_key.scope == "account" and api_key.agent_email is not None)
        or (api_key.scope == "agent" and not api_key.agent_email)
        or (api_key.scope not in {"account", "agent", "read_only", "full_access", "reviewer"})
    ):
        raise _unauthorized()

    # Keys of a soft-deleted inbox stop working until it is restored.
    if api_key.scope == "account":
        return Principal(tenant_id=api_key.tenant_id, is_admin=True, scope="account", principal_id=f"api_key:{api_key.id}")

    if api_key.agent_email:
        owner_deleted = await db.scalar(select(Agent.deleted_at).where(
            Agent.email == api_key.agent_email, Agent.tenant_id == api_key.tenant_id))
        if owner_deleted is not None:
            raise _unauthorized()

    return Principal(
        tenant_id=api_key.tenant_id,
        is_admin=False,
        scope="full_access" if api_key.scope == "agent" else api_key.scope,
        agent_email=api_key.agent_email,
        principal_id=f"api_key:{api_key.id}",
    )


async def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator token required")
    return principal


async def require_operator(principal: Principal = Depends(require_admin)) -> Principal:
    """The server operator: administrator of the default tenant. Guards settings that are global to the server."""
    if principal.tenant_id != DEFAULT_TENANT_ID:
        raise HTTPException(status_code=403, detail="Server operator credential required")
    return principal


async def require_reviewer(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin and principal.scope != "reviewer":
        raise HTTPException(status_code=403, detail="Reviewer credential required")
    return principal


async def require_read(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin and principal.scope not in {"read_only", "full_access"}:
        raise HTTPException(status_code=403, detail="Token does not permit reading messages")
    return principal


async def require_write(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin and principal.scope != "full_access":
        raise HTTPException(status_code=403, detail="Token does not permit sending messages")
    return principal


async def require_agent_access(
    email: str,
    principal: Principal = Depends(require_read),
) -> Principal:
    if not principal.is_admin and principal.agent_email != email:
        raise HTTPException(status_code=403, detail="Token is not authorized for this agent inbox")
    return principal
