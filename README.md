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

The system suite includes Native SDK/boot/services/filesystem/VM tests, board storage tests and
RPi5 image composition. QEMU exercises Native and I/O boot payloads; it does not certify real Pi hardware.

`CI_APP_CLIENT_ID` is an Actions variable and `CI_APP_PRIVATE_KEY` is the App PEM Actions secret.
The App is installed on HypeR-Proj and uses contents, pull-request, checks and actions permissions.
Only trusted coordination jobs receive App credentials. Untrusted candidate code runs in separate jobs.
Snapshot hashes are passed through trusted job outputs before downloaded artifacts are consumed.

Run coordination self tests with `python3 -m unittest discover -s tools/tests`.
`tools/configure.py --apply REPOSITORY` applies the approved default-branch policies.
The `legacy/workflows` directory preserves the original suite definitions as migration reference;
those workflows are not registered as automatic system CI.

The manifest owns checkout paths and links; CI checks out that exact layout,
including proposed manifest changes, without generating a second source tree.
System runs resolve the latest qualified QEMU and RPi5 I/O VM package digests
once and record them in the candidate snapshot. Every test uses those immutable
packages. Appliance source changes are qualified by the I/O VM repository's own
kernel workflow; system acceptance never recompiles the appliance.
