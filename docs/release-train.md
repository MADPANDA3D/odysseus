# Release Train Policy

> Active process (MAD-862). Applies to every stable Pandamonium release.

## Why trains

Stable releases ship as consolidated patch trains, not one release per pull
request. Cutting a release for every small fix repeats the full ceremony — PR
checks, main checks, tag workflows, signed assets, image builds, installed
acceptance, and notes — and produces release notes that visually collapse many
fixed issues into a single item.

## Cadence

- **Planned train:** at most one per day. A train contains every merged,
  CI-green PR since the previous tag unless the operator explicitly defers one.
- **Operator-requested train:** cut on request when the ready batch warrants it.
- **Emergency hotfix (standalone release):** reserved for data loss, updater
  failure, security exposure, or a production-stopping regression. Everything
  else waits for the next train, and the release notes must say why the fix
  could not wait.

## Roles

- **Release manager:** exactly one per train. Sole writer of merges to `main`
  (through PRs), version bumps, tags, release notes, signed assets, and
  publication. Implementation lanes never tag, publish, or run the updater.
- **Implementation lane:** owns one Linear issue and one PR. Implements, tests,
  pushes the branch, opens the PR, and stops. It never merges its own PR and
  never touches version files, `.github/releases/`, or CT103.

## Parallel implementation lanes

To keep concurrent agents conflict-free:

1. One issue per branch and PR, named `feat|fix/mad-###-slug`, based on current
   `origin/main`.
2. One isolated `git worktree` per lane under `.worktrees/`; never work in the
   shared checkout while another lane is active.
3. Declare the files the issue owns before editing. Any file that two ready
   issues must change is serialized: the second lane rebases only after the
   first merges.
4. Rebase on `origin/main` before marking a PR ready; the base branch is
   expected to move while lanes are open.
5. Never push to another lane's branch. Never force-push `main` or any tag.
6. Commit per meaningful unit using Conventional Commits with `Refs: MAD-###`.

## Checks

- Per-PR: focused, diff-aware checks plus the required repository checks. Do not
  remove a required check that protects `main`.
- The complete authoritative Python, browser, security, and release suite runs
  once on the release candidate and nightly on `main` for drift.
- A PR with red or missing required checks is not a release candidate.

## Publication

- Stable releases use immutable tags and keep Ed25519 signing, checksums,
  provenance, protected-data validation, health gates, and rollback.
- Build the multi-architecture image once from the accepted `main` SHA and
  promote that exact digest; the tag verifies the amd64/arm64 manifests instead
  of rebuilding different bits.
- Release notes are generated from the included Linear keys and commit scopes so
  a consolidated train does not collapse multiple issues into one item.
- CT103 is updated once per train, only through operator action (the signed
  updater) or an explicit manual request.
- Tag the release-PR merge commit on `main`. Tagging a release-branch prep
  commit instead leaves that release's merge commit inside the next train's
  commit range, which forces the previous release's issues into the next
  train's notes for provenance.

## Train checklist

1. Confirm every included issue is merged, its PR closed, and its Linear state
   current.
2. Cut the release branch and run the release candidate suite.
3. Generate notes from the full commit range; verify every included `MAD-###`
   key is covered and fail the train if one is missing.
4. Publish the signed release; independently verify the artifact hash, Ed25519
   signature, and image digest.
5. Update CT103 through the updater; verify health and confirm the newest two
   rollback snapshots are retained.
6. Close issues with outcome notes only after installed or documented
   automated/live proof.

## Rollback

Every train names its rollback: the previous signed release plus the updater's
retained snapshot. Never delete the previous train's artifact or rollback
snapshot during publication.
