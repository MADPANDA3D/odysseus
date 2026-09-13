# AI-GROWTH-WORKSPACE System Map

Last reviewed: [date]
Architecture owner: [owner]
Selected pattern: [workstation-first | starter single-node | separated/hybrid]

## Implementation repository

Implementation source stays outside AI-GROWTH-WORKSPACE.

Repository location:

Current branch or release:

Permitted change boundary:

Deployment target:

Known-good release:

Recovery reference:

## Outcomes served

1. [outcome and evidence]
2. [outcome and evidence]
3. [outcome and evidence]

## Role placement

| Role | Current host or provider | Job | Data handled | Owner |
| --- | --- | --- | --- | --- |
| Access edge | [location] | [job] | [data] | [owner] |
| Agent control | [location] | [job] | [data] | [owner] |
| Compute | [location] | [job] | [data] | [owner] |
| Knowledge and state | [location] | [job] | [data] | [owner] |
| Integrations | [location] | [job] | [data] | [owner] |
| Observability | [location] | [job] | [data] | [owner] |
| Operator workstation | [location] | [job] | [data] | [owner] |

## System diagram

```text
[operator]
    |
[access]
    |
[agent control] ---- [integrations]
    |                       |
[compute]             [business tools]
    |
[knowledge and state]
    |
[independent backup]

[observability] watches the roles and records current evidence.
```

Replace the labels with your roles. Add only connections that exist or are
approved for the current stage.

## Primary flows

### FLOW-001 - [name]

Trigger:

Input and authoritative source:

Processing:

Human review or approval:

External action:

Provider or system-of-record readback:

Record updated:

Manual fallback:

## Data map

| Data | Authoritative source | Working copy/index | Readers | Writers | Backup | Restore order |
| --- | --- | --- | --- | --- | --- | --- |
| [data] | [source] | [copy] | [roles] | [roles] | [backup] | [order] |

## Access and approval

| Boundary | Identity check | Permitted roles | Approval | Readback |
| --- | --- | --- | --- | --- |
| [boundary] | [method] | [roles] | [owner/action] | [source] |

## Scoped provider areas

| Provider | Product area needed | Workflow served | Permitted actions | Readback source |
| --- | --- | --- | --- | --- |
| [provider] | [specific area] | [workflow] | [actions] | [source] |

Do not expand into unrelated provider areas without a new project decision.

## Dependencies

| Component | Depends on | Failure effect | Manual fallback | Owner |
| --- | --- | --- | --- | --- |
| [component] | [dependency] | [effect] | [fallback] | [owner] |

## Backup and recovery

Important state:

Backup destination:

Backup frequency:

Last successful backup:

Last restore drill:

First restore priority:

Recovery evidence:

Recovery gate: [pass/blocked]

## Observability

| Signal | Source | Freshness | Healthy condition | Owner |
| --- | --- | --- | --- | --- |
| [signal] | [source] | [age] | [condition] | [owner] |

## Current acceptance tests

- [ ] One ranked workflow runs from trigger to provider or system-of-record readback.
- [ ] State survives a restart.
- [ ] Required data restores from an independent backup.
- [ ] Rollback or forward recovery is documented.
- [ ] Missing authorization is rejected.
- [ ] Failure produces a visible status and manual fallback.
- [ ] Evidence is stored under `evidence/`.

## Promotion triggers

Separate or expand a role only when:

- [measured capacity, availability, recovery, maintenance, or isolation trigger]
