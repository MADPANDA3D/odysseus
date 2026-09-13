AI-GROWTH-WORKSPACE EVIDENCE

This folder holds proof used to accept a stage, close a ticket, verify recovery,
or support a decision.

GOOD EVIDENCE

- current provider or system-of-record readback;
- test or smoke-test summary;
- health and capability summary;
- benchmark with workload and conditions;
- restore-drill record;
- acceptance checklist;
- version, release, or checksum record;
- dated owner review;
- redacted screenshot when text readback is unavailable.

DO NOT STORE

- credentials, tokens, private keys, recovery codes, or session material;
- full confidential customer records;
- unrelated logs or data dumps;
- evidence with no claim, date, or source.

NAMING

Use:

YYYY-MM-DD_TICKET-OR-STAGE_short-description.ext

Example:

2026-08-01_TKT-004_restore-drill.txt

EVIDENCE RECEIPT

Every evidence item should identify:

- date and timezone;
- ticket or stage;
- claim being verified;
- evidence source;
- collection method;
- observed result;
- expected result;
- pass, fail, partial, or unknown;
- limitations;
- reviewer;
- next verification date when the evidence can become stale.

EVIDENCE LABELS

OBSERVED
  Direct current inspection or provider or system-of-record readback.

OWNER-STATED
  A fact or requirement supplied by the user.

RESEARCHED
  A dated external finding with its source.

ASSUMPTION
  A plausible planning statement not yet verified.

UNKNOWN
  Required information that is not yet established.

RECOMMENDATION
  A proposed action. It is not evidence.

EVIDENCE STRENGTH

Prefer:

1. current provider or system-of-record readback;
2. repeatable test against the intended path;
3. immutable release or file identity;
4. owner observation;
5. agent summary.

An agent summary can explain evidence, but it does not replace the underlying
source. Link evidence from STATUS.md, DECISIONS.md, SYSTEM-MAP.md, and the
relevant ticket instead of copying the same payload into every file.
