<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# Native acceptance applications

These programs exercise Native interfaces inside HypeR. Their source is separate
from production applications; they use the formal `app/Cargo.toml` workspace and
lockfile so the tested SDK and userspace dependencies remain coherent.

| Program | Selection | Purpose |
| --- | --- | --- |
| [vm-smoke](vm-smoke/README.md) | `make test-vm-smoke` | Boot a privileged static `/init` fixture and exercise VM ownership, trapped MMIO, interrupts and retirement. |
| [fs-failure](fs-failure/) | `make test-filesystem-failure` | Boot a static `/init` fixture and test remote filesystem replies, backend failure, protected mappings and bounded directory traversal. |
| [terminal-probe](terminal-probe/) | Development profile; `make test-terminal` | Exercise terminal modes, editing, flow control and foreground lease restoration. |
| [retirement-probe](retirement-probe/) | Development profile; `make test-process-retirement` | Stop real children and verify private memory/register observation, endpoint closure, authority limits and revocation. |
| [lifecycle-stress](lifecycle-stress/) | Development profile; `make test-lifecycle-stress` | Seeded concurrent handle churn, capability transfer and process termination; see [fuzz/stress commands](../stress/README.md). |

The standalone init fixtures have no component registration and are built only
by their test targets. `terminal-probe` declares development-only packaging in
its `component.mk`. Ordinary system and I/O profiles do not include these
programs. Host unit tests remain with the production package they exercise.

C and SDK startup fixtures remain under [`tests/native`](../native/).
Manual hardware storage and networking workloads remain under
[`tests/hardware`](../hardware/); they are not QEMU CI performance gates.
