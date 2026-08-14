---
title: "Organizing policy (Groups)"
weight: 12
---

# Module 11: Organizing policy (Groups)

> You are in the [CPEX tutorial]({{< relref "_index" >}}). This module needs the IdP.

**Goal:** factor the setup every route shares — here, the identity resolver — into one reusable **group**, so routes join it instead of repeating it.

## The problem

By now you have written `authentication: [keycloak]` on route after route. The resolver is the same everywhere; only the per-route authorization differs. That repetition is a maintenance hazard: add a second issuer or a claim mapper later, and you have to change every route and hope you caught them all.

A **group** is a named, reusable bundle of policy — authentication steps, authorization steps, plugins — that routes opt into. Put the shared part in a group once; each route joins it and adds only what is specific to it.

## Build it

Define a top-level `groups:` section and join it from each route with `groups:`. From [`policies/m11.yaml`](https://github.com/contextforge-org/cpex/tree/main/examples/tutorial/policies/m11.yaml):

```yaml
plugins:
  - name: keycloak
    kind: identity/jwt
    hooks: [identity.resolve]
    config: { ... as in module 2 ... }

# One reusable bundle — the resolver every route needs, written once.
groups:
  identified:
    authentication:
      - keycloak

routes:
  - tool: get_compensation
    groups: identified            # inherits keycloak; no authentication: line
    authorization:
      pre_invocation:
        - "require(role.hr)"
  - tool: search_repos
    groups: identified
    authorization:
      pre_invocation:
        - "require(role.engineer)"
  - tool: send_email
    groups: identified            # still resolves the token; no role required
    authorization:
      pre_invocation:
        - "require(authenticated)"
```

The `identified` group carries the one thing all three routes share. Each route joins it and adds only its own authorization, so identity is resolved the same way everywhere while policy still decides each outcome per caller.

Two things worth knowing:

- **`groups:` is sugar over tags.** `groups: identified` is exactly `meta: { tags: [identified] }` — a route joins a group by naming it. The `groups:` field just makes that membership a first-class, discoverable spelling. Runtime tags a host injects still join groups the same way.
- **An unknown group is a load error.** Join a group that isn't defined — a typo like `groups: identifed` — and the config is rejected at load, so a mistake can't silently leave a route unauthenticated.

## Run it

```bash
cargo run -p cpex-tutorial --example m11_groups
```

```
▸ alice (hr) → get_compensation (group resolves her token, require(role.hr) passes)
  ✓ ALLOWED  { ... }

▸ evan (engineer) → get_compensation (resolved by the same group, denied at require(role.hr))
  ✗ DENIED   [...] access denied

▸ evan (engineer) → search_repos (same group, require(role.engineer) passes)
  ✓ ALLOWED  { ... }

▸ alice (hr) → search_repos (denied at require(role.engineer))
  ✗ DENIED   [...] access denied

▸ alice (hr) → send_email (group resolves her token; require(authenticated) passes)
  ✓ ALLOWED  { ... }
```

Every route resolved the caller's token through the same group, yet each outcome is decided by that route's own authorization. The resolver appears once, not three times.

## Try it

1. **Break a join.** Change one route's `groups:` to a name that doesn't exist (`groups: identifed`) and re-run. Expect: the config is rejected at load with an unknown-group error — the typo fails loudly instead of silently dropping authentication.
2. **Change the resolver once.** Add `leeway_seconds: 5` (or a second entry under `trusted_issuers:`) to the `keycloak` plugin. Every route that joins `identified` picks it up — you edited one place, not three.
3. **Tags are the same thing.** Replace `groups: identified` on a route with `meta: { tags: [identified] }` and re-run. Same result — `groups:` and a matching tag are the same membership.

## Checkpoint

{{< details "Does the route repeat the group's authentication?" >}}
No. The route has no `authentication:` block, so it inherits the group's. Identity resolution stacks broad → narrow (global → group → route); a route joining `identified` runs the group's `keycloak` resolver without naming it again.
{{< /details >}}

{{< details "Group or the reserved catch-all?" >}}
`groups:` are opt-in — a route joins one by name. That is different from applying a plugin to *every* request, which is a separate, always-on layer. Reach for a group when a *subset* of routes shares setup, as here.
{{< /details >}}

## Go deeper

- [Configuration → Groups]({{< relref "/docs/configuration" >}}) for the full `groups:` schema, defaults, and how membership resolves.

## Next

[Module 12: Delegation subjects]({{< relref "12-subjects" >}}): choose whether a minted token speaks for the caller or the gateway itself.
