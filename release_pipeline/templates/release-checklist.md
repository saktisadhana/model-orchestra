# Release Checklist

## Inherited Gates

- [ ] Every earlier phase is `GO`.
- [ ] Phase scorecards are complete and evidence hashes resolve.
- [ ] Current budget-benefit evidence still meets the declared objective.
- [ ] No unresolved P0/P1 defect.

## Offline Quality

- [ ] Canonical offline command passes without credentials/network.
- [ ] Live/paid tests are excluded by default.
- [ ] Capability, routing, budget, path, artifact, redaction, and migration tests pass.
- [ ] Wheel and sdist install tests pass on Windows and Linux.
- [ ] MCP host smoke matrix passes.

## Economics

- [ ] Prices/billing modes have source and effective date.
- [ ] Unknown pricing reports `unavailable`.
- [ ] Pending liabilities are reconciled or conservatively retained.
- [ ] No hard-budget overspend.
- [ ] Cash outlay, quota, strong-model capacity, and overage are separate.
- [ ] Default routes that fail the declared budget objective are disabled.
- [ ] Engineering and evaluation spend are recorded.

## Security and Privacy

- [ ] Immutable floors apply to every public execution path.
- [ ] Data-handling policy is enforced before provider calls.
- [ ] No secrets/private endpoints/machine paths in artifacts or docs.
- [ ] No unresolved critical/high finding.
- [ ] Untrusted execution is sandboxed or refused.

## Product

- [ ] Stable tool/config schemas are versioned.
- [ ] README quick start was tested by a non-author.
- [ ] Support matrix contains only verified hosts/providers/platforms.
- [ ] Upgrade, rollback, and uninstall pass.
- [ ] Changelog and migration notes are current.

## Release Decision

- [ ] `GO`
- [ ] `REWORK`
- [ ] `PAUSE`
- [ ] `STOP`

Reason:

Approved maximum post-release monitoring/evaluation spend:
