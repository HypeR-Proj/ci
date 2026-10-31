<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# PR buttons and whole-topic submission

Each managed repository contains the same small `.github/workflows/topic-button.yml`
listener. The App publishes a **topic** check on open PRs. Its native GitHub buttons
are in **PR → Checks → topic**. The check's **Details** link opens that exact
check, rather than the coordinator's Actions page:

- **Run system CI** verifies every open PR with the same `topic:*` label.
- **Submit topic** submits that whole verified group, after checking every member.

A PR without a topic is a one-member submission. Use one lower-case topic label per
PR and at most one PR per repository in a topic. A topic member outside the manifest,
on another base branch, or carrying multiple topics blocks submission. Draft PRs
retain their topic and cannot be submitted. Mark every member ready before testing.

The listener records the GitHub event in an immutable Actions artifact. It checks
out no source and receives no secrets. The central coordinator validates the
workflow source, artifact digest, App, PR head and actor before acting. The App key
must remain centralized in `ci`; the deployment instructions below place it in the
`topic-control` environment restricted to `main`. Do not distribute it to component
repositories. The privileged refresh workflow always checks out `main`, including
when triggered by a PR event.

System verification publishes controls before running its tests, so its PRs do not
depend on a scheduled poll for their first buttons. PR lifecycle events in `ci`
and completion of its module tests, button listener or system verification also
refresh controls. These refreshes do not automatically start system tests: a
verification request must still come from a user.

The coordinator also polls every five minutes for other repositories. GitHub can
delay schedules, so buttons may take longer to appear or take effect. If the
**topic** check is missing entirely, no controls have been published for that head;
review approval is unrelated to button visibility. Run the
[Topic coordinator workflow](https://github.com/HypeR-Proj/ci/actions/workflows/refresh.yml)
manually with `action=controls` to publish missing buttons without processing
requests or merging anything. Use `action=poll` only to process the queued button
requests sooner; polling can also advance an already requested submission. The **topic** check shows
the last request result and the members being submitted. The **system** check links
to the actual acceptance run. Repeated verification requests reuse a running test
for that target; a transport retry is reconciled against its unique request ID.

## Submission policy

Only `roolrz` and the CI GitHub App may merge default-branch PRs. The
PR-only update rule names the immutable `roolrz` user ID and App ID directly;
other administrators and contributors are not granted merge authority. Direct pushes, force
pushes and default-branch deletion remain prohibited for both. Required approval
count is zero and CODEOWNER reviews are not mandatory. CODEOWNERS still defines
who may instruct the bot to submit a topic.

**Run system CI** and manual system workflow dispatches require active organization
membership. Outside collaborators are rejected even when they can write to a
repository. **Submit topic** and `force-submit` require the sole submission owner,
`roolrz`, to remain an active member with the original GitHub account identity.
Every member repository must assign its trusted default-branch ownership to that
account. Proposed CODEOWNERS edits cannot authorize their author. The coordinator
checks identity when receiving a request and again before merging or resuming.
GitHub's check buttons are shared across viewers; unauthorized clicks receive an
explicit rejection instead of dispatching tests or starting a submission.

Normal submission requires the latest successful `module` and `system` checks on
exact heads, a common tested candidate, unchanged bases and topic membership, and
mergeable PRs. Other checks, including documentation and I/O VM qualification,
must finish without failure. No approving review is needed. Normal **Submit topic** never bypasses tests.
`roolrz` can explicitly use `action=force-submit` with a target to override tests; this still requires administrator access to every member,
a complete topic, fixed commits and normal squash merges. The App has a PR-only
override so it can execute this explicit owner request; every forced submission
records the actor and override in protected coordinator history. No other actor
can request it, and no override permits direct pushes or rewriting history.

System runs wait for the trusted main `System request authorization` workflow
before resolving or building a candidate. It checks the actual triggering actor
again on reruns. Bot dispatches must identify a previously authenticated member
request in protected `topic-state`, and membership is checked again at execution.
Private members are supported through the App's organization Members read permission.
The authorizer publishes a small artifact bound to the exact run and attempt.
Consumers verify its main-workflow provenance, digest and actor identity; a denied
or missing authorization never starts the test matrix. Branch previews consume
this evidence without receiving App credentials.

## Failures and recovery

All members are checked before the first merge. The coordinator saves the candidate
and each merge's progress on the `ci` repository's `topic-state` branch. It checks
the exact heads, bases and remaining membership again before each merge, and merges
the coordinator repository last. State writes use ordinary fast-forward Git updates;
a conflicting writer cannot overwrite an existing checkpoint.
The state branch permits creation and updates only by the App and prohibits force
pushes and deletion. The coordinator refuses to load state without these protections.
GitHub hides bypass actor lists from scoped App tokens. Administrator provisioning
therefore records the verified actors, ruleset IDs and modification timestamps in
the protected environment's `CI_TOPIC_STATE_POLICY` variable. The coordinator rejects
missing records or changed rules and still checks their active scope and rule types.
This does not require granting the App administration permissions.

GitHub cannot atomically merge multiple repositories. A failure can therefore leave
a partly submitted topic. The active record prevents another topic from being
submitted while recovery is pending. Lost API responses are reconciled with actual
PR state and the tested merge tree. Transport failures are retried by a later poll.
Changed commits, ownership or membership block further merging. Correct the reported
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
This command also refreshes the administrator-verified state policy in `topic-control`;
create that environment first, and reprovision after changing state protections.
The App needs contents, pull requests, checks and actions write permission, plus
organization Members read permission. Approve that permission on the organization
installation as well. Its
Client ID is the `CI_APP_CLIENT_ID` variable. Store `CI_APP_PRIVATE_KEY` in the
`topic-control` environment with only the `main` branch allowed, and remove any
repository-level copy after validating authentication. Routine polling never runs
candidate code with this token. The privileged workflows accept only `main` runs.

For the initial migration, the disabled-by-default `migrate-app-key.yml` workflow
can seal the existing repository secret directly for this environment's public key.
Temporarily enable `CI_APP_KEY_MIGRATION=enabled`, run it on `main`, and use its
encrypted artifact with the environment-secret REST endpoint. Validate with
`verify-app.yml`, remove the repository secret, then delete the migration variable
and artifact. The workflow never emits the PEM or accepts a replacement public key.
