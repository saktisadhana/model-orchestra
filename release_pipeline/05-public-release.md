# Phase 4: Public Beta and Core Stable Release

## Goal

Harden the proven budget-preserving workflow into a maintainable public plugin
without expanding beyond measured value.

## Entry Requirement

Phase 3 budget-benefit proof is `GO`. All earlier gates pass. If proof is
`PAUSE` or `STOP`, do not enter this phase.

## Deliverables

### Shared runtime boundaries

Extract only ownership boundaries justified by tests:

- typed configuration;
- immutable authorization policy;
- routing/economics;
- provider transport selected by ADR;
- retry/error taxonomy;
- transactional budget engine;
- verification;
- artifacts/telemetry;
- thin MCP registration.

Adapters use public runtime contracts. Module line count is a warning, not a release metric.

### Trust and compatibility

- Version public tool schemas and configuration migrations.
- Pin/test MCP SDK and supported protocol revisions.
- Define trusted-local execution and refuse untrusted execution without a conforming sandbox.
- Test path confinement, cancellation, permission denial, interrupted streams, process cleanup, and host degradation.
- Publish `SECURITY.md`, threat model, vulnerability process, and data-handling policy.

### Documentation and distribution

- README: problem, five-minute install, first budget-preserving workflow, support matrix, trust model, evidence links.
- Build/test wheel and sdist; TestPyPI before PyPI.
- Provide one-command host registration examples.
- Publish changelog, migration, upgrade, rollback, uninstall, and release checklist.

### External beta

At least three people complete the core workflow across two operating systems
and two hosts without private instructions. Capture setup time, diagnostics,
provider shape, failures, and whether cash/quota/strong-pool reports were
understandable.

## Release Gates

### Public beta

- Earlier gates pass.
- Frozen stable tool surface and prompt-schema budget.
- No unresolved P0/P1 defects.
- Reproducible artifacts and checksums.
- External users complete delegated workflow and inspect budget-benefit evidence.

### v1 Core Stable

- Every earlier release gate remains satisfied.
- Public beta soak window completes.
- Three independent successful installations/workflows.
- No unresolved critical/high security finding.
- Upgrade, rollback, and uninstall verified.
- Current evidence still meets Phase 3 economic thresholds.
- Release checklist completed.

## Stop-Loss

Do not add durable missions, adaptive budgets, more hosts, or more provider
families to rescue poor adoption. First determine whether the core
budget-preserving workflow is valuable and understandable.
