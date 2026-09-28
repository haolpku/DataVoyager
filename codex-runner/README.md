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

## Chat controller

`dist/chat.js` is a separate entry point for `datavoyager chat`. It uses the SDK's
thread start/resume APIs, read-only sandbox, disabled shell feature, structured
output, and abort signal. The supervisor passes connection settings through stdin;
model prompts contain requirements and job state, not API keys. The runner emits
thread IDs, completed-turn usage, and a structured decision. Python validates and
executes dataset actions in separate processes. It never uses the legacy runner's
`danger-full-access` default.

Run its offline contract tests with `corepack yarn build && node --test dist/chat.test.js`.
