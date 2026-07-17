# OAuth lab — hand-crafting every message

A companion to `docs/mcp-auth-id-jag.md`. Every flow the authorization server
supports, driven by hand with curl so you can watch each message on the wire.
Work through it in order — each lab builds on the previous one's artifacts.

Conventions:

```bash
export RH="https://roundhouse.karmatek.io"   # your MCP_BASE_URL
export PAT="<your dashboard token>"           # superadmin personal access token
jq --version                                  # you'll want jq
```

A JWT decoder you'll use constantly (claims only; **decoding is not verifying**):

```bash
jwt() { cut -d. -f2 <<<"$1" | tr '_-' '/+' | base64 -d 2>/dev/null | jq .; }
```

---

## Lab 1 — the token plane (no OAuth flows yet)

Slice 1 exists so you can see audience binding and scope gates work before any
grant machinery is involved.

### 1.1 The platform's public keys

```bash
curl -s $RH/.well-known/jwks.json | jq .
```

One RSA key (`kid` like `rh-…`). This is everything a spawned server needs to
validate tokens — note what's absent: no secrets, no callback URL. Stateless.

### 1.2 Mint a real token by hand

`/api/oauth/dev/mint` is a superadmin-only lab endpoint that skips the OAuth
front door and just exercises the mint + verify plane:

```bash
TOK=$(curl -s -X POST $RH/api/oauth/dev/mint \
  -H "Authorization: Bearer $PAT" -H 'content-type: application/json' \
  -d '{"server": "net-tools", "scopes": ["tools:ping"]}' | jq -r .access_token)
jwt "$TOK"
```

Read the claims against the design doc §7: `iss` (our AS), `aud` (exactly one
server), `sub` (you), `scope`, `exp` (≤ 1h — "revocation = short TTL").

### 1.3 Use it — and watch the two gates

```bash
# Gate 1+2 pass: right audience, scope covers the tool
curl -s $RH/s/net-tools/mcp -H "Authorization: Bearer $TOK" \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq .

# Gate 1 fails: same (valid!) token against a different server -> 401
curl -si $RH/s/billing-api/mcp -H "Authorization: Bearer $TOK" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -1
```

That 401 is the confused-deputy defence: a compromised server cannot replay
its callers' tokens against a neighbour. Mint another token *without*
`tools:ping` and watch the scope gate hide/deny the tool instead (403 / absent
from `tools/list`) — same middleware that gates static tokens today.

> Servers built before this branch don't verify JWTs yet — redeploy one so
> codegen emits the verifier chain (`_RhMultiVerifier`). Its old `mcps_`
> static token keeps working side by side: that's the no-flag-day migration.

---

## Lab 2 — discovery: follow the breadcrumbs from a bare 401

What Claude Code does automatically, done by hand.

```bash
# 1. Knock without a token
curl -si $RH/s/net-tools/mcp -d '{}' | head -3
# 401. Now probe the RFC 9728 well-known location for this resource path:

# 2. Protected Resource Metadata — "who am I, who can issue tokens for me"
curl -s $RH/.well-known/oauth-protected-resource/s/net-tools/mcp | jq .

# 3. Its authorization_servers[0] is the platform. Fetch the AS metadata:
curl -s $RH/.well-known/oauth-authorization-server | jq .
# (also served at /.well-known/openid-configuration for enterprise probes)
```

You now know the authorize, token, and registration endpoints without any
out-of-band configuration. Fetch a second server's PRM and diff: same AS,
different `resource` — fifty servers, fifty audiences, one desk.

---

## Lab 3 — the interim Valkyrie path (jwt-bearer, RFC 7523)

The "log in once" grant. Two setup steps, then one POST per (user, server).

### 3.1 Register a trusted client (the courier's badge)

```bash
curl -s -X POST $RH/api/oauth/clients -H "Authorization: Bearer $PAT" \
  -H 'content-type: application/json' \
  -d '{"client_name": "valkyrie", "trusted": true, "confidential": true}' | jq .
export CID="rhc_…" CSEC="rhs_…"   # from the response — secret is shown ONCE
```

`trusted: true` is the admin blessing that unlocks this grant. Try the
exchange below with an untrusted DCR client and you'll get
`unauthorized_client` — that refusal *is* the design.

### 3.2 Configure the assertion profile (the allowlist)

Tell the AS which IdP-issued assertions to accept. With Entra SSO already
configured, only the audience (the harness's own Entra app client id) is new:

```bash
curl -s -X PUT $RH/api/oauth/assertion-profiles -H "Authorization: Bearer $PAT" \
  -H 'content-type: application/json' \
  -d '{"profiles": [{"name": "entra-id-token", "enabled": true,
       "audience": "<valkyrie-entra-app-client-id>"}]}' | jq .
```

### 3.3 The exchange — one back-channel POST

Get a real Entra id_token for a user of the harness's Entra app (e.g. from the
harness's own login, or `az` tooling against that app registration), then:

```bash
curl -s -X POST $RH/oauth/token -u "$CID:$CSEC" \
  -d grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer \
  -d "assertion=$ENTRA_ID_TOKEN" \
  -d "resource=$RH/s/net-tools/mcp" \
  -d "scope=tools:ping" | jq .
```

Two factors in one request: `-u` proves *who's asking* (the courier),
`assertion` proves *who the user is* (the badge). Decode both JWTs side by
side — the assertion's `aud` is the harness's Entra app; the minted token's
`aud` is one server. The Entra token stopped at the desk (token-passthrough
ban). No refresh token comes back: cache per (user, server), re-exchange on
expiry.

Things to try: an expired assertion; a token whose `aud` is some other app;
a user who exists in Entra but not in Roundhouse (`invalid_grant` — the token
endpoint deliberately never JIT-provisions). Errors are the curriculum.

### 3.4 Introspect like a resource server would

```bash
curl -s -X POST $RH/oauth/introspect -u "$CID:$CSEC" -d "token=$TOK" | jq .
```

### 3.5 The ID-JAG future, today's config

The migration is one profile row (design §8). It's already implemented —
enable it to see the stricter checks (typ `oauth-id-jag+jwt`, `aud` = our
issuer, single-use `jti` with replay refusal):

```bash
curl -s -X PUT $RH/api/oauth/assertion-profiles -H "Authorization: Bearer $PAT" \
  -H 'content-type: application/json' \
  -d '{"profiles": [
        {"name": "entra-id-token", "enabled": true, "audience": "<harness-app-id>"},
        {"name": "id-jag", "enabled": true}]}' | jq .
```

Both rows live at once = the cutover posture. When Entra ships leg 1, Valkyrie
swaps which JWT it puts in the same `assertion` field; nothing else moves.

---

## Lab 4 — the interactive flow, PKCE by hand

The full browser dance with you as the client.

### 4.1 Register yourself (DCR, RFC 7591)

```bash
curl -s -X POST $RH/oauth/register -H 'content-type: application/json' \
  -d '{"client_name": "marty-by-hand",
       "redirect_uris": ["http://localhost:9999/cb"]}' | jq .
export LABCID="rhc_…"
```

Anonymous, instant, public client (`token_endpoint_auth_method: none`) — PKCE
will carry the proof instead of a secret.

### 4.2 Craft PKCE

```bash
VERIFIER=$(openssl rand -base64 48 | tr '+/' '-_' | tr -d '=')
CHALLENGE=$(printf %s "$VERIFIER" | openssl dgst -sha256 -binary \
            | openssl base64 -A | tr '+/' '-_' | tr -d '=')
echo "verifier:  $VERIFIER"; echo "challenge: $CHALLENGE"
```

The challenge goes out in the front channel; the verifier never leaves your
shell until token time. That asymmetry is the entire defence.

### 4.3 Authorize

Listen for the redirect, then open the authorize URL in a browser:

```bash
nc -l 9999 &     # catches the redirect so you can read the code off the wire
open "$RH/oauth/authorize?response_type=code&client_id=$LABCID\
&redirect_uri=http://localhost:9999/cb&scope=tools:ping&state=lab42\
&code_challenge=$CHALLENGE&code_challenge_method=S256\
&resource=$RH/s/net-tools/mcp"
```

You'll hit the login page (first time), then the consent page (untrusted
client, first time). Approve, and read the raw redirect in the `nc` window:
`GET /cb?code=rhac_…&state=lab42`. Check `state` matches.

### 4.4 Redeem — where PKCE pays off

```bash
curl -s -X POST $RH/oauth/token \
  -d grant_type=authorization_code -d "code=rhac_…" \
  -d redirect_uri=http://localhost:9999/cb \
  -d "code_verifier=$VERIFIER" -d "client_id=$LABCID" | jq .
```

Try it with a wrong verifier first — `invalid_grant`. Then correctly. Then
**redeem the same code again**: single-use, refused. You get an access token
(decode it — same shape as every other grant's product) and a refresh token.

### 4.5 Sessions: the "login once" property, observed

Run 4.2–4.4 again for a *different* server (`resource=$RH/s/other/mcp`).
No login page, no consent page — the browser bounces straight back with a
code. That's the AS session cookie + remembered consent doing what the field
guide promised: an OAuth *flow* per server, a *login* per lifetime.

### 4.6 Refresh rotation and theft detection

```bash
curl -s -X POST $RH/oauth/token -d grant_type=refresh_token \
  -d "refresh_token=rhrt_…" -d "client_id=$LABCID" | jq .
# -> new access token AND a NEW refresh token (rotation).
# Now replay the OLD refresh token: refused — and the whole family is revoked.
```

### 4.7 The real thing

```bash
claude mcp add --transport http net-tools $RH/s/net-tools/mcp
```

Claude Code walks Lab 2 + Lab 4 automatically. Watch the platform's auth log
(`/api/…` Logs console, context `auth`) fill with `oauth.authorize` /
`oauth.token` events carrying *your* identity — the audit upgrade from
"someone with the key" to a named human.

---

## Reference: what exists where

| Piece | Path |
|---|---|
| AS metadata / JWKS / PRM | `api/app/routes/well_known.py` |
| authorize / token / register / introspect / revoke | `api/app/routes/oauth.py` |
| admin: clients, profiles, dev mint, key rotation | `api/app/routes/oauth_admin.py` |
| signing keys (encrypted at rest) | `api/app/services/oauth_keys.py` |
| JWT mint/verify, scope intersection | `api/app/services/oauth_tokens.py` |
| client registry (manual/DCR/CIMD) | `api/app/services/oauth_clients.py` |
| assertion profiles (interim + id-jag) | `api/app/services/oauth_assertions.py` |
| codes + rotating refresh tokens | `api/app/services/oauth_flows.py` |
| generated-server verifier chain | `api/app/services/codegen.py` (`_oauth_verifier_src`) |
| tests for all of the above | `api/tests/test_oauth_as.py` |
