<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# CI test policy

The scripts in this directory are the stable boundary between GitHub Actions
and the project build/test system. They are also directly runnable locally.
The `quality` suite requires ripgrep, while the `scripts` suite requires
ShellCheck; GitHub Actions installs both tools explicitly.

| Suite | Required contract |
| --- | --- |
| `quality` | Architecture, bootstrap-stack, and IRQ-ownership boundary checks, formatting, host, Kconfig, and kallsyms tests |
| `scripts` | ShellCheck for test and guest-acquisition scripts |
| `aarch64-build` | Clippy, representative VA/PA/IPA configuration builds, canonical build, stripped-image identity, image ABI/instruction checks, and a separate kernel-self-test image |
| `aarch64-qemu` | Linux userspace and console RX plus the complete AArch64 feature markers described below |
| `riscv64-qemu` | The RISC-V Linux guest boots and hands control to `/init` |
| `x86_64-build` | Clippy and successful canonical/stripped image compilation; no runtime requirement yet |

The AArch64 QEMU matrix covers one and four CPUs on both the Armv8.0
`cortex-a72` model and the feature-rich `max` model, plus constrained-memory
and 42-bit compact-address-space cases. Together these require both nVHE/VHE
host modes, LL/SC/LSE atomics, and default and reduced host VA geometries.
Every case verifies kernel self-tests, guarded thread/IRQ/emergency stacks,
scheduler and sleeping synchronization, SMP admission, GICv3/vGIC, host and
guest timers, virtual system registers, PL011 RX, KASLR geometry, allocator
ownership statistics, lazy guest demand paging, and Linux userspace.

Failed QEMU jobs retain their complete serial logs as CI artifacts. Guest
Linux inputs are checksum-pinned by `tools/guest` and cached only as CI inputs;
they are not included in the distributable kernel artifact.

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

`check-arch-boundaries.sh` also ratchets the temporary direct dependencies from
`src/arch` into kernel policy. Each baseline entry identifies the source file,
explicit kernel contract path, and reference count. Removing a reference
therefore requires lowering the corresponding entry in the same change; adding
or substituting a contract, adding a file, or increasing a count fails the
quality suite. The lexical check is a migration ratchet, not proof of the Rust
module graph; privacy and review must also reject indirect re-exports.
