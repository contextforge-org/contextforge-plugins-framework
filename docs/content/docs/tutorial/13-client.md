---
title: "Delegation as a client"
weight: 14
---

# Module 13: Delegation `subject: client` (the agent scopes its own token)

> You are in the [CPEX tutorial]({{< relref "_index" >}}). This module needs the IdP.
>
> **Cookbook recipe:** [Recipe 5: Scope a token the agent already holds]({{< relref "/docs/identity-delegation#recipe-5-scope-a-token-the-agent-already-holds-1-leg" >}}).

**Goal:** mint a downstream-scoped token when the caller is not a person but an **agent acting as itself** — an OAuth client that authenticated to the IdP on its own behalf.

## The problem

Modules 6 and 12 both assumed a signed-in human: `subject: user` exchanges the *caller's user token*. But plenty of calls have no human behind them and are not the gateway either — an agent authenticates to the IdP as its **own OAuth client** (the `client_credentials` grant) and arrives holding a token that speaks for *itself*.

You still want least privilege at the boundary: narrow that broad client token to just the tool being called. That is `subject: client`.

It sits between the two subjects you already know:

| Subject | Whose token is exchanged? | Needs a caller token? |
|---|---|---|
| `user` (module 6, 12) | the signed-in user's | yes |
| **`client` (this module)** | **the calling agent's own** | **yes** |
| `this_workload` (module 12) | none — gateway mints its own | no |

Like `subject: user`, it scopes an **inbound** credential — so an anonymous request has nothing to exchange. Unlike `this_workload`, the authority is the *caller's*, not the gateway's.

## Build it

Two changes from module 12. First, resolve the agent's token into the **`client`** slot with `role: client`. Second, select it with `subject: client`. From [`policies/m13.yaml`](https://github.com/contextforge-org/cpex/tree/main/examples/tutorial/policies/m13.yaml):

```yaml
plugins:
  # role: client routes the inbound token to the `client` slot (not `subject`).
  - name: keycloak-agent
    kind: identity/jwt
    hooks: [identity.resolve]
    config:
      role: client                 # <-- the agent as an OAuth client
      header: Authorization
      claim_mapper: standard
      trusted_issuers:
        - issuer: http://localhost:8081/realms/cpex-tutorial
          audiences: [cpex-gateway]   # the agent's token is aud'd to the gateway
          algorithms: [RS256]
          decoding_key: { kind: jwks_url, url: "…/certs", insecure_http: true }

routes:
  - tool: search_repos
    authentication: [keycloak-agent]
    authorization:
      pre_invocation:
        - "delegate(workday-oauth, target: workday-api, audience: workday-api, subject: client)"
        - "require(delegation.granted)"
```

The agent authenticates upstream as the realm's `cpex-agent` client; CPEX validates that token, and the `delegate` step exchanges it — **one leg** (the scope), because the agent already did the authenticate leg itself.

## Run it

```bash
cargo run -p cpex-tutorial --example m13_client
```

```
▸ agent (cpex-agent client) → search_repos (subject: client — scopes the agent's own token)
  ✓ ALLOWED  { ... "repositories":[ ... ] }

▸ anonymous → search_repos (subject: client — no client token to exchange, delegation fails)
  ✗ DENIED   [delegation.bad_request] ... empty bearer_token ...
```

The agent's own token was narrowed to the `workday-api` audience and the call went through. The anonymous request had no client token to exchange, so — exactly like `subject: user` — delegation failed.

## Try it

1. **Swap to `this_workload`.** Change `subject: client` to `subject: this_workload` and re-run the anonymous case. Expect: it now **succeeds** — the gateway mints from its own credentials and needs no caller token. That is the line between "the agent's authority" and "the gateway's authority."
2. **Give the client the wrong audience.** In `realm-export.json`, remove the `audience-cpex-gateway` mapper from the `cpex-agent` client and restart the IdP. Expect: the exchange fails — Keycloak rejects a subject token whose audience doesn't include the exchanging client (`cpex-gateway`).
3. **Point a user token at it.** A user token also resolves into the `client` slot under `role: client`, so `subject: client` would scope *it* too — the subject follows the slot, not the human/machine distinction. Match `role:` to what actually arrives.

## Checkpoint

{{< details "Why does anonymous fail here but succeed under this_workload?" >}}
`subject: client` exchanges the **caller's** client token; an anonymous request has none. `subject: this_workload` uses the **gateway's own** client credentials, so there is no caller token it needs. Both mint a downstream token — the difference is whose authority it carries.
{{< /details >}}

{{< details "Is this one leg or two?" >}}
One. The agent did the authenticate leg upstream (it got its client token from the IdP itself), so CPEX performs only the **scope** leg — a plain RFC 8693 exchange. The two-leg path is [Recipe 2]({{< relref "/docs/identity-delegation#recipe-2-agent-acting-as-itself-by-its-spiffe-svid" >}}), where the agent presents a SPIFFE SVID and CPEX does both legs. Match the subject to what arrived: a JWT minted from an SVID is a `client`; the SVID itself is a `caller_workload`.
{{< /details >}}

## Go deeper

- [Recipe 5: Scope a token the agent already holds]({{< relref "/docs/identity-delegation#recipe-5-scope-a-token-the-agent-already-holds-1-leg" >}}) for the reference version and the SVID-vs-token distinction.
- [Delegation reference]({{< relref "/docs/apl/delegation" >}}) for the full `subject:` contract.

## Next

[Module 14: Passthrough]({{< relref "14-passthrough" >}}): the opposite move — when to forward the caller's token unchanged instead of minting one.
