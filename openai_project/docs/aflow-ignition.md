# A-Flow ignition

For qualifying repository mutations, Codex follows `Agents.md`: reuse a
supplied candidate plan or create one once, invoke the project `aflow` custom
agent, and pass its adapter-ready result to `audisor.audisor_lifecycle.ignite`.
The ignition layer delegates contract validation to the existing adapter and
allows implementation only when the returned contract is ready and verifiable.

Project-scoped custom agents are standalone files in `.codex/agents`. The
PreToolUse hook writes an audit record and requires a verified, contract-bound
primary lock for each intercepted mutation. Codex must trust the project for
project-local agents and hooks to load. In Codex CLI 0.144.4, PreToolUse can
surface a message and return a nonzero hook status, but cannot deny the tool
call itself; its audit is therefore observable control evidence, not a
platform-enforced write barrier. Codex also has no configuration switch that
forces every client or surface to spawn a named agent.
