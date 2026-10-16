"""Tenant model for the open core.

Every tenant-owned row carries a ``tenant_id``. A self-hosted install uses the single
``DEFAULT_TENANT_ID`` everywhere, so behaviour is unchanged. A hosted deployment registers a
principal resolver (``register_principal_resolver``) that maps its own credentials to a
``Principal`` bound to a tenant; the core then enforces the boundary.

Enforcement has two layers:

1. Explicit: a ``Principal`` carries ``tenant_id``, services take it as a required argument, and
   code that runs without a request (workers, the SMTP receiver, token links, provider feedback)
   states the tenant it acts for, always taken from a stored row (the inbox, the message), never
   from request input.
2. A safety net: once a request is authenticated its database session is bound to the tenant
   (``bind_tenant``) and every ORM select, update and delete on a tenant-owned table gets
   ``tenant_id = <bound tenant>`` appended, and every new row is stamped with it. A query that
   forgets to filter therefore cannot cross the boundary. Sessions that are not bound (workers,
   intake, startup) are deliberately unrestricted and must filter explicitly.

Inbox addresses and domains stay globally unique, so inbound routing needs no tenant: the
address resolves the inbox and the inbox carries its tenant.
"""

import re
from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

from sqlalchemy import Column, String, event
from sqlalchemy.orm import Session, with_loader_criteria

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.security import Principal

# Tenant of single-tenant installs, the admin key and every row that existed before tenancy.
DEFAULT_TENANT_ID = "default"

# Lowercase slug, 1 to 64 characters. Ids are opaque to the core; the overlay chooses them.
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

SESSION_TENANT_KEY = "tenant_id"

# Execution option that lifts the tenant filter for one statement. Only for checks that must see the
# whole table because the thing they protect is global (an inbox address or a domain name is unique
# across tenants). Never use it to read or change another tenant's data.
UNSCOPED = {"tenant_scope": False}


def validate_tenant_id(tenant_id: object) -> str:
    if not isinstance(tenant_id, str) or not TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise ValueError("tenant_id must be 1 to 64 characters of a-z, 0-9, '_' or '-', starting with a letter or digit")
    return tenant_id


class TenantOwned:
    """Mixin for every table whose rows belong to one tenant."""

    tenant_id = Column(String(64), nullable=False, index=True)


# --- credential hooks for a hosted layer ----------------------------------------------------------

# resolver(raw_token, db) -> Principal | None. May raise HTTPException (for example 401) to reject a
# credential it recognises but finds invalid, so the request never falls through as anonymous.
PrincipalResolver = Callable[[str, "AsyncSession"], Awaitable[Optional["Principal"]]]
# extractor(request) -> credential | None, for credentials the core does not know (a session cookie).
TokenExtractor = Callable[["Request"], Optional[str]]

_principal_resolvers: List[PrincipalResolver] = []
_token_extractors: List[TokenExtractor] = []


def register_principal_resolver(resolver: PrincipalResolver) -> None:
    if resolver not in _principal_resolvers:
        _principal_resolvers.append(resolver)


def unregister_principal_resolver(resolver: PrincipalResolver) -> None:
    if resolver in _principal_resolvers:
        _principal_resolvers.remove(resolver)


def get_principal_resolvers() -> List[PrincipalResolver]:
    return list(_principal_resolvers)


def clear_principal_resolvers() -> None:
    """Remove all registered resolvers (test helper)."""
    _principal_resolvers.clear()


def register_token_extractor(extractor: TokenExtractor) -> None:
    if extractor not in _token_extractors:
        _token_extractors.append(extractor)


def unregister_token_extractor(extractor: TokenExtractor) -> None:
    if extractor in _token_extractors:
        _token_extractors.remove(extractor)


def get_token_extractors() -> List[TokenExtractor]:
    return list(_token_extractors)


# --- policies a hosted layer can enforce ------------------------------------------------------------

# A policy is an async callable taking keyword context; it raises HTTPException to refuse. The core calls
# them at the moments below and has no policies of its own, so a single-tenant install is never restricted.
#   inbox.create   db, tenant_id, address
#   domain.create  db, tenant_id, domain
#   message.send   db, tenant_id, agent, recipient
Policy = Callable[..., Awaitable[None]]
POLICY_EVENTS = ("inbox.create", "domain.create", "message.send")
_policies: dict = {event: [] for event in POLICY_EVENTS}


def register_policy(event: str, policy: Policy) -> None:
    if event not in _policies:
        raise ValueError(f"unknown policy event {event!r}; expected one of {POLICY_EVENTS}")
    if policy not in _policies[event]:
        _policies[event].append(policy)


def unregister_policy(event: str, policy: Policy) -> None:
    if policy in _policies.get(event, []):
        _policies[event].remove(policy)


async def enforce_policies(event: str, **context) -> None:
    for policy in list(_policies[event]):
        await policy(**context)


# --- session binding ------------------------------------------------------------------------------

def bind_tenant(db: "AsyncSession | Session", tenant_id: str) -> None:
    """Restrict every later ORM statement on this session to one tenant and stamp new rows with it."""
    db.info[SESSION_TENANT_KEY] = validate_tenant_id(tenant_id)


def bound_tenant(db: "AsyncSession | Session") -> Optional[str]:
    return db.info.get(SESSION_TENANT_KEY)


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_criteria(state) -> None:
    tenant_id = state.session.info.get(SESSION_TENANT_KEY)
    if tenant_id is None or state.execution_options.get("tenant_scope") is False:
        return
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(TenantOwned, lambda cls: cls.tenant_id == tenant_id, include_aliases=True)
        )


def stamp_new_rows(session: Session) -> None:
    """Give every new tenant-owned row its tenant, and refuse a row that has none or the wrong one.

    Bound sessions stamp from the binding. Unbound sessions must set ``tenant_id`` themselves, so a
    forgotten tenant fails loudly at flush instead of landing in some default tenant.
    """
    bound = session.info.get(SESSION_TENANT_KEY)
    for obj in list(session.new):
        if not isinstance(obj, TenantOwned):
            continue
        if obj.tenant_id is None:
            if bound is None:
                raise ValueError(f"{type(obj).__name__} was created without a tenant_id")
            obj.tenant_id = bound
        elif bound is not None and obj.tenant_id != bound:
            raise ValueError(f"{type(obj).__name__} belongs to another tenant than this request")
