<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# System CI

This repository owns cross-component acceptance and its GitHub Actions coordination tools.
Component PRs run their own `module` checks. Full system tests are started manually through
[System verification](https://github.com/HypeR-Proj/ci/actions/workflows/system.yml).

Use `main`, `kernel#123`, or `topic:example` as the target. Topic PRs must share that exact label,
with at most one PR per repository. Candidate commits and all dependency bases are fixed for the
run. Successful results are posted only after rechecking those inputs. Unrelated input changes require a new run. Already merged topic members remain valid only
when the merged tree equals the tested tree. A scheduled metadata check invalidates stale green
checks; it does not start system tests. GitHub schedules can be delayed, so recheck the candidate
before coordinating a merge rather than treating the polling interval as an atomic guarantee.

The system suite includes Native boot/services/filesystem/VM tests, board storage tests and
Native and I/O boot payloads under QEMU. Pi5 image distribution is manually dispatched
in the pi5-images repository and never gates system verification or component merges.

`CI_APP_CLIENT_ID` is an Actions variable and `CI_APP_PRIVATE_KEY` is the App PEM Actions secret.
The App is installed on HypeR-Proj and uses contents, pull-request, checks and actions permissions.
Only trusted coordination jobs receive App credentials. Untrusted candidate code runs in separate jobs.
Snapshot hashes are passed through trusted job outputs before downloaded artifacts are consumed.

Run module tests from the workspace with `python3 project.py test ci`.
This covers the coordinator and test harness without booting a system.
See [test ownership](ci/README.md) for the 29 runtime jobs and module boundaries.
`tools/configure.py --apply REPOSITORY` applies the approved default-branch policies.
The `legacy/workflows` directory preserves the original suite definitions as migration reference;
those workflows are not registered as automatic system CI.

The manifest owns checkout paths and links; CI checks out that exact layout,
including proposed manifest changes, without generating a second source tree.
System runs resolve the latest qualified QEMU I/O VM package digest
once and record them in the candidate snapshot. Every test uses those immutable
packages. Appliance source changes are qualified by the I/O VM repository's own
kernel workflow; system acceptance never recompiles the appliance.
