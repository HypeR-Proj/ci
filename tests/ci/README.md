<!--
SPDX-FileCopyrightText: 2026 roolrz
SPDX-License-Identifier: Apache-2.0
-->

# CI test policy

The scripts in this directory are the stable repository-level boundary between
GitHub Actions and the project build/test system. Kernel-only compiler checks
and architecture suites live under `kernel/tests/ci`; this dispatcher enters
the kernel component before running them. All suites are directly runnable
locally.
The `quality` suite requires ripgrep, while the `scripts` suite requires
ShellCheck. The shared `tools` action reuses available runner commands and
installs missing Ubuntu packages. It caches only downloaded `.deb` archives;
APT still refreshes authenticated package indexes and validates the selected
archives. Cache keys include the package set, installer revision, runner
architecture and UTC date, with older matching downloads available as a fallback.
Index updates have a 60-second deadline and archive downloads a 180-second
deadline per mirror. A failed or slow download retries through the other Ubuntu
mirror; package installation starts only after downloading succeeds and is not
subject to that download deadline. This keeps slow package servers from consuming
the entire test job's budget without interrupting dpkg midway through an install.

The separate `Documentation` workflow builds the complete Markdown, Rustdoc
and Doxygen site on every pull request and push to `main`. Its build job checks
documentation diagnostics and local links in the generated HTML artifact. Only
the default-branch deployment job receives Pages write permission. See the
[documentation build guide](../../docs/documentation.md) for local reproduction
and the one-time Pages/required-check setup.

| Suite | Required contract |
| --- | --- |
| `quality` | Parsed workspace declarations, resolved Cargo graph, HAL privacy compilation, formatting, host and Loom concurrency tests, Kconfig, and kallsyms tests |
| `scripts` | Incremental build, deployment, board/package/rootfs, developer-entrypoint and QEMU transport Python tests; ShellCheck for test, acquisition and SDK scripts |
| `native` | Serial local aggregate of all AArch64 Native suites below |
| `native-sdk` | SDK publication/consumer checks, portable runtime and app tests, and service-manifest validation |
| `native-gicv3` | Serial aggregate of `native-console` and `native-vm` |
| `native-console` | GICv3 Native boot, console, apps, fleet/storage failure and runtime-crash recovery |
| `native-vm` | VM smoke on both GIC backends and forced GICv3 common-register traps |
| `native-gicv2` | GICv2 Native boot on one/four CPUs, console and runtime-crash recovery |
| `native-smp-stack` | Serial aggregate of the three SMP suites and `native-retirement-stack` |
| `native-smp-gicv3` | Guest SMP on GICv3 with four host CPUs |
| `native-smp-gicv2` | Guest SMP on GICv2 with four host CPUs and abrupt runtime-exit retirement |
| `native-smp-overcommit` | Guest SMP with one host CPU |
| `native-retirement-stack` | Power-crash retirement in dormant/pending/powered-off states and stack limits |
| `riscv64-native` | Serial aggregate of `riscv64-sdk` and `riscv64-runtime` |
| `riscv64-sdk` | RISC-V SDK publication/consumer checks, app lint and host tests |
| `riscv64-runtime` | Native static/dynamic std and application acceptance on one/four harts, file tools, paced shell input, userspace-managed guest boot, runtime-crash recovery and stack limits |
| `io-vm` | Pinned I/O appliance, cross-VM storage reset and standby acceptance on GICv2/GICv3 |
| `board-storage` | Configuration storage, Alpine rootfs, business guest, broker and userspace-device acceptance with stack watermarks |
| `aarch64-build` | Serial aggregate of the three AArch64 image build suites below |
| `aarch64-production` | Clippy, representative VA/PA/IPA configuration builds, canonical build, stripped-image identity and image ABI/instruction checks |
| `aarch64-self-test` | Separate kernel-self-test image and image ABI/instruction checks |
| `aarch64-compact` | Self-test image with 42-bit VA / 40-bit PA and image ABI/instruction checks |
| `aarch64-qemu` | Standalone kernel mechanism self-tests and the AArch64 feature markers described below |
| `riscv64-qemu` | RISC-V kernel startup, SMP admission, and standalone mechanism self-tests |
| `x86_64-build` | Clippy and successful canonical/stripped image compilation; no runtime requirement yet |

GitHub Actions runs eight AArch64 Native shards and two RISC-V Native shards
on independent runners. Each shard
builds its own prerequisites; no suite consumes mutable outputs from another.
Use `sh tests/ci/run.sh native-gicv2`, for example, to reproduce one shard.
The local aggregates stay serial because kernel feature variants and
initramfs fixtures share output paths. Do not run shards concurrently in the
same checkout. The three AArch64 image variants also build on separate runners;
their artifacts are named `aarch64-kernels-production`, `aarch64-kernels-self-test`
and `aarch64-kernels-compact`. Kernel QEMU jobs download only their image variant.
Source quality runs alongside builds; only kernel QEMU jobs wait for image builds.
All checks must still pass. Sharding reduces elapsed time by doing independent
work in parallel; it does not remove test cases or reuse a test result from a
previous commit, and the independent builds can increase total runner minutes.

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

The standalone AArch64 runner explicitly uses multithreaded TCG. With at least
three CPUs, its wait-arbitration test races two remote resolvers through Armed
and queued waits, then checks worker retirement; smaller configurations require
an explicit skip marker. Host `quality` additionally runs Loom scenarios
against production publication/admission mechanisms. See the commands and
coverage limits in [validation coverage](../../kernel/tests/VALIDATION.md).
These are correctness tests; storage/network performance benchmarks remain
manual and have no CI throughput or latency thresholds.

Failed QEMU jobs retain their complete serial logs as CI artifacts. Guest
Linux inputs are checksum-pinned by `kernel/tools/guest` and cached only as CI
inputs; they are not included in the distributable kernel artifact.

Final bootstrap-stack sizes have Rust const assertions in each architecture's
layout owner. Image validation independently checks the linked initial stack's
symbol span. Both require at least 256 KiB for bounded, allocation-free firmware
discovery; neither depends on how a constant is spelled.

`check-license-headers.sh` requires every project-authored tracked text file to
carry SPDX copyright and Apache-2.0 identifiers. The complete license text and
Cargo-generated lockfiles are intentionally exempt.

`check-workspace.py` parses Cargo manifests to enforce in-tree ABI ownership,
installed-SDK consumption and app-local sharing. It recognizes renamed packages,
workspace dependencies, target-specific tables and patches. Apps cannot declare
a direct `hyper-sys` dependency. Normal compilation resolves imports; comments
and documentation mentioning a crate are not dependency violations. The check
also reads Git index modes to reject submodules and obsolete component locks.

`kernel/tests/ci/hal-boundary.py` checks Cargo's resolved dependency direction
and compiles public/private HAL import probes. The negative probe must fail with
rustc E0603 at the intended import, with no unrelated compiler errors. Network
errors or a broken public interface cannot count as a successful privacy test.
There are no per-rule shell wrappers.

Ownership traits and consuming IRQ API signatures are checked against the real
types in `kernel/tests/kernel/type_contracts.rs` whenever `kernel-self-test` is
compiled, including the AArch64, RISC-V and x86-64 build checks.

See [validation coverage](../../kernel/tests/VALIDATION.md) for subsystem
coverage and the limits of these checks. Do not add grep/sed assertions over
function bodies or `include_str!` tests over Rust implementations. Such checks
freeze a spelling or statement order without executing the invariant. Use a
compiler check, a behavioral test of production code, or an artifact/runtime
test appropriate to the property. Assembly/weak-memory ordering and unsafe
lifetime proofs still require review; a source pattern was never a substitute.
