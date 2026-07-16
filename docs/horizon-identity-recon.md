# Competitive Recon: Prefect Horizon Identity Stack → What Roundhouse Should Borrow

**Date:** 2026-07-16
**Status:** Recon complete — feeds the build plan in [`mcp-auth-id-jag.md`](mcp-auth-id-jag.md)
**Related:** [`entra-sso-plan.md`](entra-sso-plan.md) (Phase 1, shipped)

Prefect Horizon (https://www.prefect.io/horizon) is the most direct competitor
Roundhouse has: an enterprise MCP server platform — deploy, registry, gateway,
tool-level RBAC, audit — hosting FastMCP servers. This document dissects how
their identity/governance layer actually works, compares it against our locked
ID-JAG design, and lists what's worth borrowing. Sources were Horizon's real
product docs (`docs.horizon.prefect.io`), Prefect's own blog, WorkOS case-study
material, the FastMCP source tree (Apache-2.0, readable), and the MCP
authorization spec lineage through the final 2026-07-28 release.

---

## 1. How Horizon actually does identity

### 1.1 The stack, bottom to top

| Layer | What it is | Build or buy? |
|---|---|---|
| Identity broker / OAuth AS | **WorkOS AuthKit** — SSO (SAML/OIDC), SCIM directory sync, DCR, CIMD, admin portal | **Bought.** Prefect's blog ("Why Prefect Chose WorkOS for Enterprise Auth") says AuthKit was "plug and play" from day one of FastMCP Cloud. |
| Token validation | FastMCP OSS `AuthKitProvider` / `JWTVerifier` — audience-bound (RFC 8707) token checks in the server framework | Open source (theirs). |
| Gateway | Proprietary multi-tenant reverse proxy: authenticates, resolves roles, filters capabilities, records request metadata **before server code runs** | Built. SaaS-only — no self-hosted option exists. |
| Policy/audit control plane | Org roles → server roles → capability policies; request logs + usage analytics | Built. |

**The headline: their "65+ identity integrations" is the WorkOS connector
catalog resold.** The provider list on their marketing page matches WorkOS's
catalog verbatim. Prefect did essentially zero first-party SAML/SCIM
engineering. Their identity breadth is a vendor dependency — and a structural
reason they can never ship self-hosted/air-gapped.

### 1.2 RBAC model

Three layers (docs.horizon.prefect.io/roles.md, /platform/authorization.md):

- **Org roles:** `Admin` (unrestricted, cannot be blocked per-server) and
  `Member` (needs explicit grants or server defaults).
- **Server roles:** built-in `admin` / `editor` / `viewer`; custom roles are
  Enterprise-tier only. Resolution precedence: org admin > explicit grant >
  server default > no access.
- **Capability policies (tool-level):** per-role default permissions plus
  per-tool/resource/prompt overrides. Semantics worth copying exactly:
  *unconfigured = unfiltered; once any policy exists, unmatched capabilities
  are denied* (opt-in default-deny). The `admin` role always stays allowed —
  a lockout-recovery guarantee.

Notable weaknesses: it's role+ACL, **not** attribute- or scope-based — their
API keys have *no independent scopes* ("Keys lack independent permission
scopes; Horizon evaluates the owner's current role... on each request").
Roundhouse's scoped tokens are already stronger there. Also, some capability
changes **require redeployment** — the same rebuild limitation we have with
codegen-baked scopes, so they haven't solved dynamic policy either. And
despite marketing "tied to IdP roles and groups," their docs show no actual
IdP-group→role mapping mechanics — our shipped `role_mappings` table +
`claim_mapping.py` engine is arguably ahead of what they document.

### 1.3 Audit model

Per-request gateway logs (method, actor, accept/reject, routing, full
request/response payloads viewable), usage analytics (calls, unique actors,
error rate, p95), live tail. **Retention: 3 days standard, 30 days
Enterprise.** No documented SIEM export, log schema, immutability, or
management-plane audit trail. It's request observability branded as audit.

Roundhouse already has the two halves they don't cleanly separate:
`audit_events` (management-plane, append-only, redacted) and `request_events`
(data-plane metadata). Our gap is only per-user attribution (`client_id` is a
token name today, not a person) — which is exactly what ID-JAG's `sub` claim
fixes.

### 1.4 Client-side auth UX

- Per-server toggle: "Horizon Authentication" on/off.
- Capable clients (Claude Code `/mcp`, Claude connectors, Cursor, ChatGPT
  dev-mode) get a browser OAuth sign-in on first connect; OAuth 2.1 + PKCE +
  DCR auto-provisioned per server (AuthKit does the heavy lifting).
- Non-interactive: `fmcp_`-prefixed API keys pasted into headers; personal
  keys inherit the user's access; service-account keys for automation; two
  active keys for rotation.
- Clean error contract: 401 = bad credential (rejected at gateway before
  compute), 403 = policy-denied, 404 = no server.

### 1.5 The genuinely novel feature: external authentication (credential brokering)

(docs.horizon.prefect.io/platform/external-authentication.md) — "one identity
to enter the platform, another credential to call a downstream service."
Three modes: **per-user OAuth** (Horizon stores and refreshes each user's
downstream tokens), **per-user API key**, **shared API key**. For hosted
servers the gateway *swaps the caller's Horizon credential for the downstream
credential before the request reaches server code* — transparent to the
server. This is their answer to the confused-deputy problem and the feature
most worth matching long-term. Roundhouse's `remote_headers` (shared secret
from env) is the "shared API key" mode only.

### 1.6 Other competitive facts

- Hosted servers are **Python/FastMCP only**, Streamable HTTP POST only — **no
  SSE server-push**.
- Registry: designated owners, version pinning/approval, deprecation flags,
  separate discover/use/manage permissions.
- Remix: compose up to 10 servers into one virtual endpoint; excluded tools
  are *removed from `tools/list`*, not just blocked; upstream changes appear
  next request, no redeploy; remixes can be permissioned more strictly than
  sources.
- Roadmap (founder's blog): progressive tool disclosure ("playbooks"), a
  permissioned chat interface ("Agents").
- Pricing is quote-driven; custom roles, 30-day retention, and SCIM are all
  Enterprise-gated.

---

## 2. Verdict on our locked ID-JAG design

**The design in [`mcp-auth-id-jag.md`](mcp-auth-id-jag.md) is the right
architecture, now with the spec behind it.** The industry survey found three
patterns for enterprise MCP identity:

1. **Point clients at the customer's IdP directly** (Microsoft APIM/Entra
   pattern) — breaks on Entra's missing DCR/CIMD, pushes per-server app
   registrations onto every customer. Fine as an escape hatch, wrong as the
   product.
2. **Bundle Keycloak** — proven AS, but heavy ops for a self-hosted appliance
   and *no RFC 8707 resource indicators*, which fights our
   many-dynamic-servers-per-platform model (audience must be faked with
   per-client scope mappers).
3. **Platform runs its own small AS, federates identity to the customer IdP,
   mints short-lived audience-bound per-server tokens** — what Zuplo bundles,
   what obot does, what WorkOS sells as SaaS, and what the **final MCP spec
   (2026-07-28) standardizes as Enterprise-Managed Authorization (EMA)**,
   built on the same ID-JAG drafts our design doc cites.

We independently designed pattern 3. Horizon, by contrast, outsourced its AS
to WorkOS — the thing EMA now routes around. For Federal/air-gapped buyers,
"no third-party identity broker in the trust chain" is both our architecture
and our pitch.

---

## 3. The borrow list

Ordered roughly by leverage. Items marked **[amend design]** are deltas to add
to `mcp-auth-id-jag.md`; the rest confirm or refine its §10 build inventory.

### 3.1 From FastMCP OSS (Apache-2.0 — directly adoptable code/patterns)

1. **`JWTVerifier` replaces `StaticTokenVerifier` in codegen** — the single
   smallest change with the most leverage. FastMCP's verifier already does
   JWKS fetch with 1-hour cache, issuer/audience list-intersection validation,
   `scope`-string *and* `scp`-list claim extraction, and an `ssrf_safe` mode.
   Generated `server.py` points at Roundhouse's JWKS URL; enforcement stays
   in-container and stateless, exactly as the ID-JAG doc assumes.
2. **`MultiAuth`-style verifier chaining** (`server/auth/auth.py:510`) — run
   the existing static-token verifier *and* the JWT verifier side by side
   during migration. No flag-day for existing tokens. **[amend design:** make
   dual-verifier the explicit migration mechanism rather than
   either/or codegen modes.**]**
3. **RFC 9728 + RFC 6750-correct 401 behavior** — serve
   `/.well-known/oauth-protected-resource/servers/{name}` per server (one
   platform, many audiences), and copy FastMCP's middleware nuance: missing
   token → 401 with *no* `error` attribute (triggers client discovery);
   invalid token → 401 with `error="invalid_token"` and an actionable
   description. This is what makes Claude/Cursor/VS Code auto-discover auth.
4. **The `AuthCheck(AuthContext) → bool` predicate pattern** for tool-level
   RBAC: enforced identically at **list-time (visibility filtering)** and
   **call-time (execution)**, with deliberately ambiguous "not found or not
   authorized" errors. Our `_PlatformMiddleware` already filters lists and
   gates calls on scopes; upgrading its input from static token scopes to JWT
   claims (`sub`, groups, scopes) gets us Horizon-grade capability policy
   with code we already ship.
5. **OAuthProxy design decisions** for the interactive flow: mint our own
   JWTs, never forward upstream (Entra) tokens; transaction-state in
   pluggable KV storage; refresh tokens stored hashed; PKCE forwarded
   end-to-end; consent interstitial against confused-deputy;
   `allowed_client_redirect_uris` wildcard validation; SSRF-safe metadata
   fetching. Our shipped `oidc.py` client + APP_KEY crypto already cover
   several of these primitives.

### 3.2 From the MCP spec / client-compat recon **[all amend design]**

6. **Support CIMD alongside DCR and manual client registration.** Claude
   clients now lead with CIMD (client_id = an HTTPS metadata URL Anthropic
   hosts); VS Code does classic DCR; Cursor wants pre-registered clients.
   Supporting all three covers the big clients; FastMCP's `CIMDClientManager`
   is a reference implementation.
7. **Serve the OIDC discovery alias** (`/.well-known/openid-configuration`)
   alongside RFC 8414 — added to the spec in 2025-11-25 because that's what
   enterprise tooling actually probes.
8. **Don't hard-require `resource=`** (RFC 8707) from clients — several still
   omit it. Bind audience server-side at token mint; validate audience in the
   verifier.
9. **Keep static-bearer servers on a non-OAuth-advertising endpoint.** Known
   Claude Code bug (anthropics/claude-code#59467): a static `Authorization`
   header is *ignored* whenever the server advertises OAuth. Mixing modes on
   one path will strand existing token users.
10. **Track EMA (2026-07-28 spec) as the endgame** — our Resource-AS must
    eventually advertise `urn:ietf:params:oauth:grant-profile:id-jag`,
    verify `typ: oauth-id-jag+jwt`, keep a per-tenant trusted-IdP allowlist,
    and replay-protect `jti`. Okta ships it today; Entra will follow. The
    design doc already targets this; the spec landing makes it a headline
    ("first self-hosted MCP platform with EMA support" is available to us).

### 3.3 From Horizon's product design (patterns, not code)

11. **Capability-policy semantics:** unconfigured = unfiltered; any policy
    present = default-deny for unmatched primitives; admin role always
    retained for recovery. Cleaner than our current `deny_unlisted` boolean —
    adopt as the semantics for the scope-aware grants ("third dimension" on
    `role_mappings`) in the design doc.
12. **Error contract at the edge:** 401 before compute / 403 policy-denied /
    404 no server. Cheap to standardize across platform-api and generated
    servers.
13. **Service accounts + dual active keys for rotation** — our
    `ServerToken` model is close; add a first-class service-account identity
    (a `User` row with `auth_source="service"`) so machine callers appear in
    audit with a real principal, and allow two live tokens per name.
14. **Registry governance vocabulary:** designated owner (we have
    `server_owners`), version pinning/approval, deprecation flags, and
    *separate discover/use/manage permissions*. Low-cost, high-perceived-value
    enterprise features for a later phase.
15. **External authentication / downstream credential brokering** (per-user
    OAuth to upstream services, gateway swaps credentials before server code)
    — roadmap item, post-ID-JAG. Our proxy codegen (`generate_proxy_py`) is
    the natural injection point; we already do the "shared API key" mode via
    `remote_headers`.
16. **Remix-style composition** — noted, not urgent. If we ever build it, the
    differentiating detail is *removal* of excluded tools from `tools/list`
    (invisibility, not rejection), which our middleware already knows how to
    do per-scope.

### 3.4 Where we should attack, not borrow

- **Audit:** ship SIEM-friendly structured export (JSONL/syslog/webhook) and
  customer-controlled retention on `audit_events` + `request_events`, then
  contrast with Horizon's 3–30 day, no-export story. We're one `sub` column
  and an exporter away from beating their "enterprise audit" outright.
- **Self-hosted trust chain:** their identity story requires WorkOS in the
  loop; ours terminates at the customer's own IdP. Say this loudly in
  positioning.
- **No paywall on roles:** custom roles/SCIM/retention are Enterprise-gated
  upsells for them. An open-source platform shipping capability policies and
  IdP mapping un-gated is a wedge.
- **SSE/server-push and non-Python servers:** Horizon's hosted tier supports
  neither; Roundhouse's container model doesn't care what's inside.

---

## 4. Immediate next steps

1. Amend `mcp-auth-id-jag.md` with §3.2 deltas (CIMD, OIDC-discovery alias,
   lenient `resource=`, split endpoints, dual-verifier migration, EMA grant
   profile) and adopt Horizon's capability-policy semantics for the
   scope-aware grants schema.
2. Build order stays as the design doc's §10, with the FastMCP `JWTVerifier`
   swap as the first codegen change and `MultiAuth` chaining as the
   compatibility bridge.
3. Add the audit exporter + `sub` attribution to the build inventory — it's
   cheap and it's the sharpest competitive contrast available.
4. Monitor `https://docs.horizon.prefect.io/llms.txt` (full doc index) for
   changes; founder's blog (jlowin.dev) telegraphs roadmap (playbooks,
   Agents).
