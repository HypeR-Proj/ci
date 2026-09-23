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
| `scripts` | Incremental build, deployment, board/package/rootfs, developer-entrypoint and QEMU transport Python tests; ShellCheck for test, acquisition and SDK scripts |
| `native` | Serial local aggregate of the four AArch64 Native suites below |
| `native-sdk` | SDK publication/consumer checks, portable runtime and app tests, and service-manifest validation |
| `native-gicv3` | Native boot, console, apps, runtime-crash recovery, VM smoke on both GIC backends, and forced GICv3 common-register traps |
| `native-gicv2` | GICv2 Native boot on one/four CPUs, console and runtime-crash recovery |
| `native-smp-stack` | Guest SMP on both GIC backends, host overcommit, runtime/power crash retirement and stack limits |
| `riscv64-native` | RISC-V SDK publication/consumer checks, Native static/dynamic std and application acceptance on one and four harts, file tools, paced shell input, userspace-managed guest boot and runtime-crash recovery |
| `io-vm` | Pinned I/O appliance, cross-VM storage reset and standby acceptance on GICv2/GICv3 |
| `board-storage` | Configuration storage, Alpine rootfs, business guest, broker and userspace-device acceptance with stack watermarks |
| `aarch64-build` | Clippy, representative VA/PA/IPA configuration builds, canonical build, stripped-image identity, image ABI/instruction checks, and a separate kernel-self-test image |
| `aarch64-qemu` | Standalone kernel mechanism self-tests and the AArch64 feature markers described below |
| `riscv64-qemu` | RISC-V kernel startup, SMP admission, and standalone mechanism self-tests |
| `x86_64-build` | Clippy and successful canonical/stripped image compilation; no runtime requirement yet |

GitHub Actions runs the four Native suites on independent runners. Each suite
builds its own prerequisites; no suite consumes mutable outputs from another.
Use `sh tests/ci/run.sh native-gicv2`, for example, to reproduce one shard.
The local `native` aggregate stays serial because kernel feature variants and
initramfs fixtures share output paths. Do not run shards concurrently in the
same checkout. Source quality runs alongside builds; only kernel QEMU jobs wait
for their image-producing job. All checks must still pass.

The architecture QEMU runtime suites deliberately build with
`kernel-self-test` and use an empty initramfs. They do not select a guest.
Production images instead mount the firmware initramfs and start Native
`/init`; the separate `native` suite assembles that initramfs from the in-tree
SDK and application sources and verifies the complete boot contract.
The RISC-V Native image includes userspace VM provisioning. Its Native suite
checks Linux guest startup on one and four host harts and runtime-crash recovery;
this does not imply multi-vCPU RISC-V guest support.

Native acceptance reports each phase transition with elapsed times. Each phase
has a 90-second progress deadline (`QEMU_BOOT_TIMEOUT_SECONDS`), and the complete
multi-command run has a 300-second deadline (`QEMU_NATIVE_TIMEOUT_SECONDS`).
The workflow retains the raw Native console log alongside runtime-crash logs,
including on failure, so a stalled phase can be distinguished from exhaustion
of the overall test budget.
Ordinary command submissions wait for the expected shell prompt count, so a
child's final output cannot trigger input intended for its successor. Interactive
guest console and std input probes instead use their own readiness markers.

The AArch64 QEMU matrix covers one and four CPUs with the VHE-capable `max`
model, plus constrained-memory and 42-bit compact-address-space cases. FEAT_VHE
is required; the CPU's Arm version is not sufficient. Successful boots require
canonical upper kernel/KASLR geometry and private lower Process roots. The UP
case also boots an unsupported `cortex-a72` and uses QMP register inspection to
prove it reaches the dedicated FEAT_VHE rejection loop before MMU setup.
Every case verifies kernel self-tests, guarded thread, IRQ and
emergency stacks, scheduler and sleeping synchronization, SMP admission,
the selected GICv2/GICv3 backend, host and guest timers, virtual system registers, PL011 RX, KASLR
geometry, allocator ownership statistics, and lazy guest demand paging.
The separate Native suite validates guest userspace and interactive console
input. The kernel matrix also requires Native dispatcher validation, bounded Channel
transaction tests, and the AArch64 VHE raw-code EL0 proof: repeated direct
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

`check-arch-boundaries.sh` invokes `hal-boundary.py graph` to inspect Cargo's
resolved dependency direction: `hyper-hal` depends on `hyper-core`, never the
kernel binary. `check-arch-facades.sh` also compiles positive HAL imports and a
negative private-architecture import probe. Rust privacy enforces the exported
crate boundary. These checks do not prove synchronization or lifecycle safety;
behavioral tests and review remain necessary.
