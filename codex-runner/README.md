# codex-runner

Node.js bridge used by DataVoyager's acquisition worker. It runs the Codex SDK,
streams structured events, and returns a final result to the Python supervisor.

```bash
corepack yarn install --immutable
corepack yarn build
```

The Python runtime launches `corepack yarn dev -` with a prompt file and provider
configuration. Configure the model through DataVoyager rather than putting credentials
in this directory. The runner defaults to `danger-full-access`; run it in a dedicated
execution environment. Its Python supervisor enforces the overall timeout.

This directory is included in source distributions but not the Python wheel.
