<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# CI test policy

The scripts in this directory are the stable repository-level boundary between
GitHub Actions and the project build/test system. Kernel-only source contracts
and architecture suites live under `kernel/tests/ci`; this dispatcher enters
the kernel component before running them. All suites are directly runnable
locally.
The `quality` suite requires ripgrep, while the `scripts` suite requires
ShellCheck; GitHub Actions installs both tools explicitly.

| Suite | Required contract |
| --- | --- |
| `quality` | Architecture, bootstrap-stack, and IRQ-ownership boundary checks, formatting, host, Kconfig, and kallsyms tests |
| `scripts` | ShellCheck for test and guest-acquisition scripts |
| `native` | AArch64 SDK publication/consumer checks, portable runtime tests, Native apps, and userspace-managed Linux guests in nVHE/VHE |
| `riscv64-native` | RISC-V SDK publication/consumer checks, Native static/dynamic std and application acceptance on one and four harts, file tools, and paced shell input |
| `aarch64-build` | Clippy, representative VA/PA/IPA configuration builds, canonical build, stripped-image identity, image ABI/instruction checks, and a separate kernel-self-test image |
| `aarch64-qemu` | Standalone kernel mechanism self-tests and the AArch64 feature markers described below |
| `riscv64-qemu` | RISC-V kernel startup, SMP admission, and standalone mechanism self-tests |
| `x86_64-build` | Clippy and successful canonical/stripped image compilation; no runtime requirement yet |

The architecture QEMU runtime suites deliberately build with
`kernel-self-test` and use an empty initramfs. They do not select a guest.
Production images instead mount the firmware initramfs and start Native
`/init`; the separate `native` suite assembles that initramfs from the in-tree
SDK and application sources and verifies the complete boot contract.
The RISC-V Native image currently selects a console/session/shell service graph
without VM provisioning; missing VM lifecycle capabilities remain unavailable.

Native acceptance reports each phase transition with elapsed times. Each phase
has a 90-second progress deadline (`QEMU_BOOT_TIMEOUT_SECONDS`), and the complete
multi-command run has a 300-second deadline (`QEMU_NATIVE_TIMEOUT_SECONDS`).
The workflow retains the raw Native console log alongside runtime-crash logs,
including on failure, so a stalled phase can be distinguished from exhaustion
of the overall test budget.
Ordinary command submissions wait for the expected shell prompt count, so a
child's final output cannot trigger input intended for its successor. Interactive
guest console and std input probes instead use their own readiness markers.

The AArch64 QEMU matrix covers one and four CPUs on both the Armv8.0
`cortex-a72` model and the feature-rich `max` model, plus constrained-memory
and 42-bit compact-address-space cases. Together these require both nVHE/VHE
host modes, LL/SC/LSE atomics, and default and reduced host VA geometries.
The VHE cases additionally require a canonical upper kernel/KASLR geometry
with private lower Process roots; nVHE retains the equivalent lower host
geometry. Every case verifies kernel self-tests, guarded thread, IRQ and
emergency stacks, scheduler and sleeping synchronization, SMP admission,
GICv3/vGIC, host and guest timers, virtual system registers, PL011 RX, KASLR
geometry, allocator ownership statistics, and lazy guest demand paging.
The separate Native suite requires repeated BusyBox sleeps before guest console
RX, proving timer delivery across successive interrupt retirements after guest
`/init`. The kernel matrix also requires Native dispatcher validation, bounded Channel
transaction tests, and the AArch64 VHE/nVHE raw-code EL0 proof: repeated direct
`abi_query`, scheduling and lifecycle calls, Event creation/signal/wait,
contained breakpoint fault,
Process/Thread join, and acknowledged retirement.

Failed QEMU jobs retain their complete serial logs as CI artifacts. Guest
Linux inputs are checksum-pinned by `kernel/tools/guest` and cached only as CI
inputs; they are not included in the distributable kernel artifact.

`check-boot-stack-contract.sh` protects the bounded scratch headroom required
by allocation-free firmware discovery on every architecture. Image validation
also checks the linked stack-symbol span, so source declarations and delivered
artifacts must agree on the minimum.

`test-boot-stack-contract.sh` verifies that comments and duplicate declarations
cannot satisfy this source ratchet.

`check-license-headers.sh` requires every project-authored tracked text file to
carry SPDX copyright and Apache-2.0 identifiers. The complete license text and
Cargo-generated lockfiles are intentionally exempt.

`check-arch-facades.sh` prevents migrated CPU-lifecycle, Linux guest-ABI,
host-interrupt, host-memory, host-platform, host-time, and
hardware-virtualization mechanisms from returning to the legacy flat `arch`
namespace. Extend this topical contract list as each architecture domain
completes its migration.

`check-arch-boundaries.sh` permits only the selected architecture bootstrap
adapters to call the kernel directly. Its allowlist is exact by architecture,
boot contract, and reference count; exception, interrupt, failure, and VM-exit
mechanisms cannot be added as migration debt. The lexical check is not proof of
the Rust module graph; privacy and review must also reject indirect re-exports.
