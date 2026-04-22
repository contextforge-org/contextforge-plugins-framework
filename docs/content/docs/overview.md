---
title: "Overview"
weight: 10
---

# What Are Plugins?

Plugins let you intercept and modify execution at well-defined points — without changing the targeted application code.

You define **hooks** in your application where you want extensibility. Plugins attach to those hooks and run automatically whenever they fire. The plugin manager handles registration, ordering, execution, timeouts, and error isolation. You get a deterministic pipeline with no surprises.

## How the Pipeline Works

```
Application  →  Hook Point  →  Plugin Manager  →  Application continues  →  Result
                                     │
                              ┌──────┼──────┐
                              ▼      ▼      ▼
                          Plugin A  Plugin B  Plugin C
                         (priority  (priority  (priority
                            10)       20)       100)
```

When a hook fires, the plugin manager dispatches the payload to every registered plugin in priority order. Each plugin can:

- **Allow** execution to continue unchanged
- **Modify** the payload (e.g., redact sensitive data, inject defaults)
- **Block** execution with a violation (e.g., deny a prohibited tool call)

## What You Can Build

CPEX is designed for modern AI and agent systems, but works for any application that needs safe, modular extensibility.

- **Security** — access control, prompt injection detection, data loss prevention
- **Observability** — request tracing, audit logging, metrics collection
- **Governance** — policy enforcement, compliance validation, approval workflows
- **Reliability** — rate limiting, circuit breakers, response validation

## Built-in Hooks

CPEX ships with hooks for common AI operations — tools, prompts, resources, agents, HTTP requests, identity resolution, and a unified Common Message Format for cross-cutting policy evaluation. You can also register your own hooks for any domain.

## Next Steps

Ready to build? The [Quick Start]({{< relref "/docs/quickstart" >}}) gets you a working plugin in five minutes.
