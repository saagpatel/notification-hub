# AGENTS.md - notification-hub Ops Templates

## Review guidelines

Treat LaunchAgent and hook templates as machine-mutation contracts. Review
labels, paths, ports, environment variables, log locations, executable names,
and startup behavior exactly; a small drift can make operator recovery target
the wrong service.

Hooks must remain additive and fail-soft. Review changes for behavior that
blocks Claude Code or Codex when notification-hub is unavailable, changes
upstream hook ordering, emits secrets, or sends notifications outside the
localhost/operator-approved boundary.

Docs and scripts that install, unload, bootstrap, kickstart, or inspect runtime
state must name the exact command and expected state. Do not let a template
change imply live machine state changed unless the reviewed workflow actually
performs and verifies that mutation.
