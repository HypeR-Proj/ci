<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# PR buttons and whole-topic submission

Each managed repository contains the same small `.github/workflows/topic-button.yml`
listener. The App publishes a **topic** check on open PRs. Its native GitHub buttons
are in the PR's Checks tab:

- **Run system CI** verifies every open PR with the same `topic:*` label.
- **Submit topic** submits that whole verified group, after checking every member.

A PR without a topic is a one-member submission. Use one lower-case topic label per
PR and at most one PR per repository in a topic. A topic member outside the manifest,
on another base branch, or carrying multiple topics blocks submission. Draft PRs
retain their topic and cannot be submitted. Mark every member ready before testing.

The listener records the GitHub event in an immutable Actions artifact. It checks
out no source and receives no secrets. The central coordinator validates the
workflow source, artifact digest, App, PR head and actor before acting. The App key
is configured only in `ci`; it is not distributed to component repositories.
Do not grant untrusted users write access to the coordinator repository or its secrets.

The coordinator polls every five minutes. GitHub can delay schedules, so buttons
may take longer to take effect. Run the [Topic coordinator workflow](https://github.com/HypeR-Proj/ci/actions/workflows/refresh.yml)
manually with `action=poll` to process requests sooner. The **topic** check shows
the last request result and the members being submitted. The **system** check links
to the actual acceptance run. Repeated verification requests reuse a running test
for that target; a transport retry is reconciled against its unique request ID.

## Submission policy

Only the GitHub App can update a default branch, and only through PR merges.
The normal GitHub Merge button and `gh pr merge` cannot bypass the coordinator,
including for administrators. The separate history rules prohibit force-pushing
and deleting default branches for every actor, including the App.

The coordinator requires write permission in every member repository. Normal
submission checks the latest successful `module` and `system` checks on the exact
heads, a common system candidate snapshot, unchanged dependency bases and topic
membership, mergeability, and CODEOWNER approval with resolved conversations.
Administrators may bypass review approval. They still need passing tests when
using **Submit topic**.

An administrator can explicitly bypass tests using the central workflow's
`action=force-submit` and `target=topic:example`. This requires administrator access
to every member repository and records the override. It still requires a whole
topic, fixed commits, mergeable PRs and normal squash merges. Re-running a workflow
uses the current triggering actor's permissions rather than inheriting the original
actor's privileges.

## Failures and recovery

All members are checked before the first merge. The coordinator saves the candidate
and each merge's progress on the `ci` repository's `topic-state` branch. It checks
the exact heads, bases and remaining membership again before each merge, and merges
the coordinator repository last. State writes use ordinary fast-forward Git updates;
a conflicting writer cannot overwrite an existing checkpoint.
The state branch permits creation and updates only by the App and prohibits force
pushes and deletion. The coordinator refuses to load state without these protections.

GitHub cannot atomically merge multiple repositories. A failure can therefore leave
a partly submitted topic. The active record prevents another topic from being
submitted while recovery is pending. Lost API responses are reconciled with actual
PR state and the tested merge tree. Transport failures are retried by a later poll.
Changed commits, reviews or membership block further merging. Correct the reported
condition and click **Submit topic** on a remaining member as the original actor,
using the same override setting if applicable. The original snapshot is retained;
recovery does not silently choose new commits or omit an unmerged member.

Completed snapshots remain under `submissions/` on `topic-state`, independently
of Actions artifact retention.

## Topic label retirement

The coordinator deletes only `topic:*` labels. It waits until all labeled PRs and
issues across the organization are closed, including drafts, and at least 24 hours
have passed since the last closure. Unused newly created labels are retained.
Cleanup pauses while system verification or a submission is in progress.

Before deleting labels, it saves the topic name, member URLs and closure information
under `topics/` on `topic-state`, indexed by `state.json`. It rechecks open membership
before deletion. Deleting a GitHub label also removes it from historical PRs; use
these archives to recover the group. A reopened PR after retirement needs a topic
label assigned again.

## Installing another repository

Install the organization App on the repository, register the component in the
manifest, and copy `tools/topic-button.yml` exactly to its `.github/workflows/`
directory through a reviewed PR. Keep the listener byte-identical to the coordinator
template; altered listener artifacts are rejected. Use the existing per-repository
module workflow and documentation workflow.

After confirming the native button reaches the coordinator, an administrator runs
`python3 tools/configure.py --apply REPOSITORY` to enable its submission rules.
Before the coordinator's first poll, run
`python3 tools/configure.py --apply --state-only ci` to protect its state branch
without changing the current default-branch merge policy.
The App needs contents, pull requests, checks and actions write permission. Its
Client ID and PEM key remain the `CI_APP_CLIENT_ID` variable and
`CI_APP_PRIVATE_KEY` secret in the `ci` repository. Routine polling never runs
candidate code with this token. The privileged workflows accept only `main` runs.
