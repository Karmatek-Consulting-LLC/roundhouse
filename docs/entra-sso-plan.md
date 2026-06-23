# Entra ID SSO + MCP Authorization — Plan

> Status: **Phase 1 in progress** (build started 2026-06-23). Authored 2026-06-22.
> Scope locked in discussion: single-tenant Entra ID, dashboard SSO first, MCP server OIDC next.
> This doc is the durable source of truth (committed in-repo so it survives the planned folder rename).
> All §7 open items resolved 2026-06-23.

---

## 1. Current state (what we're integrating into)

Two **independent** auth systems exist today. Don't conflate them.

### Dashboard auth (humans → web UI)
- Single backend choke point: every protected route depends on `current_user` → `resolve_token`.
  - `api/app/deps.py:12` — `current_user()` / `require_superadmin()`
  - `api/app/auth.py:77` — `resolve_token()`; bearer wire format `{id}|{raw}`, sha256-compared against `personal_access_tokens`
- Single frontend context: `frontend/src/lib/auth.tsx` — `AuthProvider`, token in `localStorage`, `login(email,password)`.
- Login UI: `frontend/src/components/login-page.tsx`; endpoints under `api/app/routes/auth.py`.
- Roles: `users.role` is free-text `String(20)` (`api/app/models.py:51`), in practice `superadmin` | `user`. Plus team memberships (`team_memberships`, own per-team role) and server ownership (`server_owners`).
- Schema is Laravel/Sanctum-derived (`personal_access_tokens`, `tokenable_*`, `App\Models\User`).

### MCP server auth (agents/clients → MCP servers) — SEPARATE, unaffected by Phase 1
- Per-server static bearer tokens (`server_tokens`), baked into generated `server.py` via FastMCP 3.3.1 `StaticTokenVerifier`.
- Scope enforcement: `require_scopes(...)` (AND semantics) on resources; in-process `_PlatformMiddleware` for tools/prompts.
- Codegen: `api/app/services/codegen.py`. Token mgmt: `api/app/services/server_auth.py`.

### Traefik
- No forward-auth / auth middleware. All auth is in FastAPI. (`traefik/traefik.yml`, `docker-compose.yml`.)

---

## 2. Decisions locked in (from discussion)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Tenancy | **Single-tenant** Entra ID |
| 2 | Provisioning | **JIT** — create user on first successful Entra login |
| 3 | Role mapping | **UI-editable mapping table**: Entra **app role** → Roundhouse role (+ optional team grant). NOT raw name-match. **Confirmed 2026-06-23.** |
| 4 | Sync authority | **Entra is authoritative** for SSO users — re-sync role + team memberships on every login (gives real deprovisioning) |
| 5 | Local auth | **Keep as break-glass** — local password admin exempt from sync |
| 6 | MCP server OIDC | **Dashboard only for now**; MCP is the next feature. Build Phase 1 modules so they're reusable for it. |

**Why a mapping table, not name-match:** Roundhouse authz has 3 grant dimensions (`users.role`, team memberships, server ownership). A flat Entra `roles` claim mapped by name can only ever set `users.role`. A table can also drive team membership and — critically — generalizes into the `claim → scopes` engine Phase 2 needs.

**Use Entra app roles, not group claims:** app roles arrive in the `roles` claim, are scoped to the app, and avoid the `groups`-claim **>200-group overage** problem (Entra replaces the claim with a Graph API pointer you'd then have to resolve).

---

## 3. Phase 1 — Dashboard SSO (build now)

**Approach:** OIDC **Authorization Code + PKCE**, validated **server-side**, then **mint the existing personal-access-token**. Everything downstream of login (the `AuthProvider` session) is unchanged — we only change how the token is issued.

**Estimate:** ~1–2 weeks production-grade (incl. mapping table + sync + tests); ~3–4 days bare-bones (JIT everyone as `user`, no mapping UI).

### Backend
- New endpoints: `GET /api/auth/oidc/login` (redirect to Entra) + `GET /api/auth/oidc/callback` (code exchange → validate ID token against Entra JWKS: verify `iss`/`aud`/`exp`/signature → upsert user → run mapping/sync → issue PAT).
- Library: `authlib` or `msal`.
- **Connection config is dashboard-managed, NOT env** (decided 2026-06-23): tenant id, client id, client secret, redirect URI live in `platform_settings` (see `app/services/sso_config.py`, edited via Settings → Entra ID SSO). The client secret is encrypted at rest with the `app.crypto` AES envelope (keyed off `APP_KEY`). `APP_KEY` remains the only env var SSO needs (it also signs the login transaction cookie).

### Frontend
- "Sign in with Microsoft" button → redirect; callback route stashes the returned token into the existing `AuthProvider`. Minimal change.

### Schema migrations
- `users`: add `oidc_sub`, `auth_source` (`local` | `entra`).
- **`users.password_hash` is currently `NOT NULL`** (`api/app/models.py:49`) — Entra-only users have no password. Make nullable OR store an unusable-password sentinel. (Small but real gate.)
- New table `role_mappings`: `entra_app_role` → `roundhouse_role` (+ optional `team_id` / team role).

### Sync guardrails
- Local (`auth_source = local`) users exempt from sync — never wiped.
- **Never let sync demote the last superadmin** — hard floor.

### Build these as standalone, reusable modules (for Phase 2)
- **OIDC client**: discovery + JWKS cache + token validation. Reused verbatim by MCP.
- **Claim → grant mapping engine**: input = claims; output = grants. Dashboard wants `{role, teams}`; MCP will want `{scopes}`. Same engine, different output target.
- Only the browser redirect/callback + PAT minting is dashboard-specific.

---

## 4. Phase 2 — MCP server auth (next feature)

**Context that reshapes the target:** MCP shipped **Enterprise-Managed Authorization (EMA)** (stable 2026-06-18). The spec direction is now clear, so don't invent a homegrown per-server OIDC scheme — align to the standard.

**Target model:** generated MCP servers act as OAuth **resource servers**:
- Implement **RFC 9728 (Protected Resource Metadata)** — return `401` + `WWW-Authenticate` pointing to a PRM doc naming the authorization server.
- Validate **audience-bound JWT access tokens** (token minted for *that* server); enforce scopes from token claims.
- Spec **forbids token pass-through** — each server is its own protected resource.
- Swap `StaticTokenVerifier` → JWT verifier (FastMCP already ships an Entra OAuth integration + JWT verifiers).
- Reuse the Phase 1 `claim → grant` engine, now emitting `{scopes}`.

**EMA / ID-JAG** = the enterprise convenience layer on top (IdP issues an **ID-JAG**, `draft-ietf-oauth-identity-assertion-authz-grant`, exchanged for an MCP access token; no per-server consent). Treat as a **future increment on the resource-server foundation**, not part of the first MCP cut.

---

## 5. Entra-specific gotchas (bank these)

1. **Entra does NOT support Dynamic Client Registration (DCR)** — deliberate product decision. Vanilla MCP OAuth assumes DCR; Entra needs **manual app registration or pre-authorized clients**. Affects **Phase 2 only** — Phase 1 dashboard SSO uses a single pre-registered app, no DCR.
2. **EMA is Okta-first.** Entra ID not on the initial supported-IdP list as of 2026-06-18. ID-JAG draft is IdP-agnostic in principle, but EMA-via-Entra may not exist yet — gate Phase 2's EMA increment on it landing.
3. **Group-claim overage** (>200 groups) — avoid by using app roles (see §2).

---

## 6. References
- Enterprise-Managed Authorization — https://blog.modelcontextprotocol.io/posts/enterprise-managed-auth/
- The New Stack writeup — https://thenewstack.io/mcp-gets-its-missing-enterprise-authorization-layer/
- RFC 9728 (PRM) — https://datatracker.ietf.org/doc/html/rfc9728
- MCP authorization tutorial — https://modelcontextprotocol.io/docs/tutorials/security/authorization
- Entra + pre-authorized clients (DCR limitation) — https://techcommunity.microsoft.com/blog/azuredevcommunityblog/building-mcp-servers-with-entra-id-and-pre-authorized-clients/4508453
- FastMCP Azure/Entra OAuth — https://gofastmcp.com/integrations/azure

---

## 7. Open items — RESOLVED 2026-06-23
- ~~Confirm decision #3 (mapping table vs raw name-match).~~ **→ Mapping table.** (See §2.)
- ~~Entra app registration is tenant-side work…~~ **→ Marty owns the tenant** (lab setup). The app-registration checklist (redirect URI, client secret, app roles in the manifest, user/group assignment) is tracked in §8 below for him to action.
- ~~Decide who issues MCP access tokens in Phase 2.~~ **→ Roundhouse acts as the OAuth authorization server.** This is the bigger build, but it lets us support **DCR** (Dynamic Client Registration) — which Entra deliberately does not — so vanilla MCP OAuth clients work out of the box. Reshapes §4: generated MCP servers stay resource servers; Roundhouse (not Entra) mints the audience-bound access tokens, brokering the upstream Entra identity. The Phase 1 OIDC client + claim→grant engine remain the reusable foundation.

## 8. Entra app-registration checklist (Marty / tenant owner)
Single pre-registered app for Phase 1 dashboard SSO (no DCR needed here):
1. Entra admin center → App registrations → New registration. Single-tenant.
2. Redirect URI (Web): `https://<roundhouse-host>/api/auth/oidc/callback` (and `http://localhost:8000/api/auth/oidc/callback` for local dev).
3. Certificates & secrets → new client secret → copy the value.
4. Token configuration / App roles → define app roles (e.g. `Roundhouse.Admin`, `Roundhouse.User`); these surface in the `roles` claim.
5. Enterprise applications → assign users/groups to those app roles.
6. Hand back: **Tenant ID**, **Client ID**, **Client secret** → enter them in Roundhouse under **Settings → Entra ID SSO → Connection** (not env vars). The page shows the exact redirect URI to register in step 2.
7. In Roundhouse: add role mappings (Settings → Entra ID SSO → Role mappings) mapping each Entra app role to a Roundhouse role / team.
