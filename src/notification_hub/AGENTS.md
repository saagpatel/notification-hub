# AGENTS.md - notification-hub Source

## Review guidelines

Treat daemon source changes as local operating-surface changes. Review
localhost binding, event validation, identity mapping, classifier precedence,
suppression, durable inbox writes, retention, and health diagnostics as
merge-relevant when they can misroute, drop, duplicate, or overstate
notifications.

Security-sensitive review should focus on mutation and delivery boundaries:
Slack webhooks, terminal notifications, action proposals, package exports,
review endpoints, log reads, and any path that touches Keychain-backed secrets
or machine-local files. Do not accept code that sends externally, reads broader
logs, or exposes secret-bearing config without explicit operator intent and a
clear failure/recovery path.

Health and burn-in output must stay truthful. A reachable daemon is not the
same as delivery readiness; review should flag docs or code that hide degraded
notifier, webhook, LaunchAgent, hook-template, storage, or port-binding state.
