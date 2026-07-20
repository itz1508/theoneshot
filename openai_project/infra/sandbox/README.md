# Audisor Phase 2B sandbox

Build the product-owned image before execution:

```text
docker build -t audisor-sandbox:phase2b-v1 infra/sandbox
```

`DockerSandboxRunner` starts a new disposable container for each validation
command. It only bind-mounts the isolated workspace at `/workspace`; it never
mounts the product, target, references, Docker socket, host home, or host temp.
It also uses no network, a read-only root filesystem, UID/GID 65534, dropped
capabilities, `no-new-privileges`, PID/memory/CPU limits, and a `/tmp` tmpfs.
There is no host-execution fallback.
