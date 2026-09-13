# AI-GROWTH-WORKSPACE Agent Operating Contract

These rules apply to any LLM or agent working in AI-GROWTH-WORKSPACE.

## Start every session

Read in this order:

1. `AGENTS.md`
2. `STATUS.md`
3. `HANDOVER.md`
4. `MEMORY.md`
5. `BUGS.md`
6. `SYSTEM-MAP.md`
7. the active ticket, if any
8. only the artifacts named by the current ticket or next action

Summarize the current objective, accepted decisions, blocker, and next action
before proposing work.

## Project-root safety gate

- Treat the directory containing this `AGENTS.md` as the candidate project
  root, then resolve it to an absolute path.
- Before project-local writes, confirm the current task was opened from this
  root or one of its descendants, or that the owner explicitly selected this
  exact existing root.
- Do not infer a project root from the task title or a name mentioned in chat.
- If the task is outside the intended project, stop and identify the exact
  directory to open. Do not create a same-named nested substitute.

## Source priority

Use the strongest current source:

1. current provider or system-of-record readback;
2. current files and configuration;
3. accepted decisions and evidence receipts;
4. dated workspace status and handover;
5. owner statements;
6. assumptions.

If sources conflict, surface the conflict. Do not silently choose the more
convenient answer.

## Evidence labels

Label material facts as:

- OBSERVED
- OWNER-STATED
- RESEARCHED
- ASSUMPTION
- UNKNOWN

`RECOMMENDATION` is a proposed action, not evidence. Do not present an
assumption or recommendation as current state.

## Scope and sequence

- Work on one bounded stage or ticket at a time.
- Ask one question at a time when the answer changes cost, architecture,
  privacy, authority, or sequence.
- Prefer equipment and services already available when they pass the need.
- Put future ideas in `BACKLOG.md`.
- Write generated project artifacts under `AI-GROWTH-WORKSPACE/artifacts/`.
- Keep control files at the workspace root.
- Keep implementation source in a separate repository. Use `SYSTEM-MAP.md` to
  record its location, current branch or release, and permitted change boundary.
- Do not change an external system merely because it is connected.
- Limit provider research and execution to the product area required by the
  selected workflow.

## Tickets

Use a ticket for implementation, repair, deployment, migration, or another
task that may pause, fail, require approval, or need evidence.

A ticket must include:

- request and business reason;
- permitted target and excluded scope;
- acceptance evidence;
- approval boundary;
- rollback or safe-stop path;
- current state and next action.

Use these states:

- `open/` for approved but unclaimed work;
- `in-progress/` for the one active implementation ticket;
- `completed/` after acceptance and provider or system-of-record readback;
- `failed/` when work stops without meeting acceptance;
- `blocked/` when a named dependency or owner action prevents progress;
- `skipped/` when the owner deliberately declines or defers the work.

## External and consequential actions

Before sending any client-facing SMS, email, or WhatsApp message, show the user
the exact final draft and wait for explicit approval. For another outbound or
public action, show the exact content, recipient or account, channel, and
timing.

Before purchasing, deleting, changing access, deploying, moving money, or
modifying another person or system:

1. show the exact intended action;
2. name the target and expected effect;
3. identify cost, visibility, and rollback;
4. obtain the required owner approval;
5. perform only the approved action;
6. read back the result from the provider or system of record;
7. store an evidence receipt.

Never place credentials or unredacted confidential records in prompts,
tickets, logs, examples, or generated documentation.

Ask the owner to confirm the approved provider credential area or
local/server-side secret store. Never invent a secret path or ask the owner to
paste secret values into chat.

## Recovery gate

Do not replace an accepted path until:

- an independent backup is identified;
- a restore drill passes without overwriting accepted state;
- rollback or forward recovery is documented; and
- the recovery evidence is linked from the ticket.

## Completion

A command result or agent summary is not enough. Mark work complete only when:

- the acceptance condition is met;
- provider or system-of-record readback confirms any external result;
- failure and rollback state are known;
- evidence is linked;
- `STATUS.md`, the ticket, and `HANDOVER.md` agree.

## End every session

Update:

1. the active artifact or ticket;
2. `DECISIONS.md` for accepted choices;
3. `MEMORY.md` for new durable facts;
4. `BUGS.md` for new known defects;
5. `STATUS.md` with current state and next action;
6. `HANDOVER.md` with the exact resume point.

Leave AI-GROWTH-WORKSPACE ready for another operator or agent to continue
without reconstructing the session.
