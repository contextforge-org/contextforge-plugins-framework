## CPEX Architecture Overview

The CPEX plugin framework follows a two-phase lifecycle: **bootstrap** (startup) and **execution** (runtime). At application startup, the host creates a `PluginManager`, registers plugin factories for each supported `kind`, and loads a YAML config file. The config loader parses and validates the file, then the manager uses the factories to instantiate each declared plugin and registers them into the `PluginRegistry` (sorted by priority and indexed by hook name). After calling `initialize()`, the manager is ready and can be shared across the application. At runtime, hook call sites simply invoke the manager with a payload and routing metadata; the manager resolves which plugins apply (via routes, policy groups, and tags), runs them through an execution pipeline, and returns a result. The diagrams and code sketch below illustrate both phases.

### CPEX Bootstrap

```mermaid
sequenceDiagram

autonumber

participant App as Host Application
participant Mgr as PluginManager<br>cpex-core
participant Fac as PluginFactoryRegistry<br>cpex-core
participant CL as ConfigLoader<br>cpex-core
participant Cfg as config.yaml
participant Reg as PluginRegistry<br>cpex-core

note over App,Reg: Application Startup

App->>Mgr: PluginManager::new
App->>Mgr: register_factory[kind, factory]
Mgr->>Fac: store factory by kind

App->>Mgr: load_config_file[path]
Mgr->>CL: load_config[path]
CL->>Cfg: read file
Cfg-->>CL: YAML content
CL->>CL: parse + validate
CL-->>Mgr: CpexConfig

loop for each plugin in config.plugins
    Mgr->>Fac: get[plugin.kind]
    Fac-->>Mgr: factory
    Mgr->>Fac: factory.create[plugin_config]
    Fac-->>Mgr: PluginInstance [plugin + handlers]
    Mgr->>Reg: register_multi_handler[plugin, config, handlers]
    Reg->>Reg: create PluginRef [trusted config]
    Reg->>Reg: index handlers by hook name + sort by priority
end

App->>Mgr: initialize.await
loop for each registered plugin
    Mgr->>Reg: get plugin_ref
    Reg-->>Mgr: PluginRef
    Mgr->>Mgr: plugin.initialize.await
end

note over App,Reg: Ready — Manager and plugins available for use
```

### CPEX Plugin Execution Flow (simplified)

```mermaid
sequenceDiagram

autonumber

participant App as Host application
participant Core as PluginManager<br>cpex-core
participant Host as cpex-hosts
participant P1 as Plugin 1
participant P2 as Plugin 2
participant P3 as Plugin 3

App->>Core: invoke(hook, payload, context)
Core->>Core: validate + normalize
Core->>Core: build execution plan

Core->>Host: invoke P1
Host->>P1: run(hook, payload)
P1-->>Host: transform / allow / deny / observe
Host-->>Core: normalized result
Core->>Core: merge result

Core->>Host: invoke P2
Host->>P2: run(hook, payload)
P2-->>Host: error / decision / mutation
Host-->>Core: normalized result
Core->>Core: apply error policy / mode semantics

Core->>Host: invoke P3
Host->>P3: run(hook, payload)
P3-->>Host: result
Host-->>Core: normalized result

Core->>Core: finalize output
Core-->>App: result
```

#### Code Sketch

```rust
// ---------------------------------------------------------------------------
// Application startup — build and initialize the manager once
// ---------------------------------------------------------------------------

async fn bootstrap() -> PluginManager {
    let mut mgr = PluginManager::default();

    // Register factories so the manager knows how to create each plugin kind
    mgr.register_factory("builtin/identity", Box::new(IdentityFactory));
    mgr.register_factory("builtin/pii", Box::new(PiiGuardFactory));
    mgr.register_factory("builtin/audit", Box::new(AuditLoggerFactory));

    // Load config — parses YAML, instantiates plugins via factories,
    // registers them into the PluginRegistry sorted by priority
    mgr.load_config_file(Path::new("plugins.yaml")).unwrap();

    // Initialize all plugins (open connections, warm caches, etc.)
    mgr.initialize().await.unwrap();

    mgr
}

// ---------------------------------------------------------------------------
// Hook call sites — invoke the manager wherever hooks fire
// ---------------------------------------------------------------------------

async fn handle_tool_call(mgr: &PluginManager, tool: &str, user: &str, args: &str) {
    let payload = ToolInvokePayload {
        tool_name: tool.into(),
        user: user.into(),
        arguments: args.into(),
    };

    // Extensions carry routing metadata (entity type, name, tags)
    let extensions = Extensions {
        meta: Some(Arc::new(MetaExtension {
            entity_type: Some("tool".into()),
            entity_name: Some(tool.into()),
            ..Default::default()
        })),
        ..Default::default()
    };

    // Pre-invoke — the manager resolves which plugins fire for this
    // tool, runs the 5-phase pipeline, and returns the result
    let (result, bg) = mgr.invoke::<ToolPreInvoke>(payload.clone(), extensions.clone(), None).await;

    if !result.continue_processing {
        // A plugin denied the call — surface the violation
        let v = result.violation.unwrap();
        eprintln!("Denied by '{}': {}", v.plugin_name.unwrap_or_default(), v.reason);
        bg.wait_for_background_tasks().await;
        return;
    }

    // ... execute the actual tool ...

    // Post-invoke — threads the context table from pre-invoke
    let (_, bg) = mgr.invoke::<ToolPostInvoke>(payload, extensions, Some(result.context_table)).await;
    bg.wait_for_background_tasks().await;
}
```