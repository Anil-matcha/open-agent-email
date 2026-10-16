# Tenancy

Every stored row that belongs to a customer carries a `tenant_id`. A normal install has exactly one tenant,
`default`: the admin key and all data belong to it, and nothing about the API changes.

## Model

- A tenant id is 1 to 64 characters of `a-z`, `0-9`, `_` or `-`, starting with a letter or digit.
- `Principal.tenant_id` says which tenant a credential acts for. `is_admin` means administrator of that tenant only.
- Inbox addresses and domain names stay unique across all tenants. Inbound mail needs no tenant: the address
  finds the inbox and the inbox carries its tenant. A domain registered by one tenant cannot be used for inboxes
  by another.
- Server settings, the outgoing relay configuration and `/metrics` belong to the server, not a tenant. Only the
  default tenant's admin (the operator) can read or change them.
- Idempotency keys are scoped per tenant.

## Enforcement

1. An authenticated request's database session is bound to the principal's tenant. Every ORM select, update and
   delete on a tenant-owned table gets `tenant_id = <tenant>` added, and every new row is stamped with it, so a
   query that forgets to filter cannot cross the boundary.
2. Code that runs without a request (the outbound and webhook workers, SMTP intake, capability links, provider
   feedback) is not bound. It takes the tenant from a stored row and sets it explicitly; an unbound session that
   creates a row without a tenant fails at flush.
3. Live feeds, webhook subscriptions, and delivery are matched on tenant as well as inbox.

## Plugging in accounts

A hosted layer registers a resolver; the core never learns what an account is.

```python
from app.tenancy import register_principal_resolver, register_token_extractor
from app.security import Principal

async def resolve(token, db):
    ...  # look the credential up; return None if it is not yours; raise HTTPException(401) to reject it
    return Principal(tenant_id="acme", is_admin=True, principal_id="account:123")

register_principal_resolver(resolve)
register_token_extractor(lambda request: request.cookies.get("session"))  # optional
```

Resolvers run after the admin key and stored API keys. Stored keys carry the tenant of the account that made them.

## Tests

`server/tests/test_tenant_isolation.py` seeds two tenants and sweeps every REST route and MCP tool from the
OpenAPI document and tool list, asserting that a request aimed only at another tenant's resources leaks nothing and
changes nothing in the database. It also covers webhooks, events, WebSockets, local delivery between tenants and
the session guard. A route or tool it cannot fill in fails the suite until it is mapped.
