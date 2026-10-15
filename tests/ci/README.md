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
| `quality` | Parsed workspace declarations, resolved Cargo graph, HAL privacy compilation, formatting, host and Loom concurrency tests (including optional heap profiling), Kconfig, and kallsyms tests |
| `scripts` | Incremental build, deployment, board/package/rootfs, developer-entrypoint and QEMU transport Python tests; ShellCheck for test, acquisition and SDK scripts |
| `native` | Serial local aggregate of all AArch64 Native suites below |
| `native-sdk` | SDK publication/consumer checks, portable runtime and app tests, and service-manifest validation |
| `native-gicv3` | Serial aggregate of the GICv3 boot, interactive, failure and VM suites |
| `native-fixture` | Build and package the ordinary Native kernel/initramfs pair; `CI_ARCH` selects AArch64 (default) or RISC-V |
| `native-boot` | Native boot and application startup on one/four CPUs |
| `native-console` | Paced input, pressure, shell restart and guest console |
| `native-terminal` | Terminal modes, transformations and flow control |
| `native-apps` | Native command-line application acceptance |
| `native-shell` | Shell language, editing, completion and redirection |
| `native-services` | Service readiness, scoped control, captured stdio, query and restart limits; early progress alongside unsuppressed logs and failed-console recovery |
| `native-fleet` | Fleet configuration admission and error handling |
| `native-storage-recovery` | Storage readiness and handoff failure recovery |
| `native-filesystem` | Filesystem RPC and provider failure |
| `native-runtime-crash` | Guest runtime failure and isolation in both fleet orders |
| `native-vm` | VM smoke on both GIC backends and forced GICv3 common-register traps |
| `native-gicv2` | GICv2 Native boot on one/four CPUs, console and runtime-crash recovery |
| `native-smp-stack` | Serial aggregate of the three SMP suites and `native-retirement-stack` |
| `native-smp-gicv3` | Guest SMP on GICv3 with four host CPUs |
| `native-smp-gicv2` | Serial aggregate of the following two GICv2 suites |
| `native-smp-gicv2-guest` | Guest SMP on GICv2 with four host CPUs |
| `native-smp-gicv2-retirement` | GICv2 SMP topology with abrupt runtime-exit retirement |
| `native-smp-overcommit` | Guest SMP with one host CPU |
| `native-retirement-stack` | Power-crash retirement in dormant/pending/powered-off states and stack limits |
| `native-stack-16k` | Native userspace boot on one/four CPUs with 16 KiB kernel thread stacks, independent RAMFS test root, root/supervision handoff, shell commands, static/dynamic std and application startup |
| `riscv64-native` | Serial aggregate of `riscv64-sdk` and `riscv64-runtime` |
| `riscv64-sdk` | RISC-V SDK publication/consumer checks, app lint and host tests |
| `riscv64-runtime` | Serial aggregate of the five ordinary Native probes with `CI_ARCH=riscv64`, plus `riscv64-vm` |
| `riscv64-vm` | RISC-V fleet configuration, VM authority on one/four harts, runtime-crash recovery and stack limits |
| `io-vm` | Pinned I/O appliance, cross-VM storage reset and standby acceptance on GICv2/GICv3 |
| `board-storage` | Serial aggregate of the six board suites below; all use 16 KiB kernel thread stacks and stack watermarks |
| `board-storage-media` | Configuration storage, cold restart and Native shell |
| `board-boot-system` | Independent root, supervision handoff and disk-independent/provider failure recovery |
| `board-alpine-rootfs` | Alpine ext4 root, write/sync/restart and checksums |
| `board-business` | Managed guest start/stop/restart and physical block operations |
| `board-broker` | Broker fairness, endpoint loss, active disks and Native storage |
| `board-userspace-device` | Native physical device backend through MMIO/IRQ capabilities |
| `aarch64-build` | Serial aggregate of the three AArch64 image build suites below |
| `aarch64-production` | Clippy, representative VA/PA/IPA configuration builds, canonical build, stripped-image identity and image ABI/instruction checks |
| `aarch64-self-test` | Separate kernel-self-test image and image ABI/instruction checks |
| `aarch64-compact` | Self-test image with 42-bit VA / 40-bit PA and image ABI/instruction checks |
| `aarch64-qemu` | Standalone kernel mechanism self-tests and the AArch64 feature markers described below |
| `riscv64-qemu` | RISC-V kernel startup, SMP admission, and standalone mechanism self-tests |
| `x86_64-build` | Clippy and successful canonical/stripped image compilation; no runtime requirement yet |

GitHub Actions runs the Native boot, console, terminal, app and shell probes
on separate runners. Two producer jobs build one immutable kernel/initramfs pair
per architecture. Twelve probe jobs download the matching artifact from the
**same workflow run**, check its revision, architecture and SHA-256 hashes, then
skip only the image-building prerequisites. GICv2 and GICv3 consume the same
AArch64 images. These consumers install QEMU only; they do not rebuild the SDK,
Rust std, applications or guest payload. No cross-commit build/test result cache
is trusted. The artifact consists of the two images and their manifest; mutable
Cargo output and disks are never shared.

Feature-specific Native tests, each of the six board acceptance suites, and
RISC-V VM/retirement tests run independently and build their own prerequisites.
This preserves the forced GICv3 trap variant, minimum stacks and failure-injection
features without mixing their outputs with the ordinary fixture. The board broker
keeps its full fairness/lifecycle sequence in one runner because later assertions
depend on earlier transitions. Runtime diagnostics remain separate artifacts.

Use `sh tests/ci/run.sh native-console` to build and run a single probe locally,
or set `CI_ARCH=riscv64` for the RISC-V fixture. `CI_NATIVE_FIXTURE` is optional;
when supplied, it must point to a validated artifact for the current commit and
architecture. An invalid artifact fails instead of silently rebuilding. The
`native`, `native-gicv3`, `native-gicv2`, `riscv64-runtime` and `board-storage`
aggregates remain serial for local reproduction. Do not run suites concurrently
in one checkout: feature variants and initramfs fixtures share output paths.

The three AArch64 kernel image variants also build on separate runners; their
artifacts are named `aarch64-kernels-production`, `aarch64-kernels-self-test`
and `aarch64-kernels-compact`. Kernel QEMU jobs download only their image variant.
Quality, SDK and independent runtime builds start without waiting for fixture
producers. All checks must pass; no test is dropped or weakened to reduce time.
Parallelism trades additional runners for a shorter critical path. Actual elapsed
time still includes runner queueing and artifact transfer; compare workflow
start-to-finish and longest-job duration when measuring a change.

The architecture QEMU runtime suites deliberately build with
`kernel-self-test` and use an empty initramfs. They do not select a guest.
Production images instead mount the firmware initramfs and start Native
`/init`, then mount an independent system volume before the root and supervision
handoff. The separate `native` suite assembles a synthetic no-device fixture
from the in-tree SDK and application sources. Its test-only init copies a formal
payload into a distinct RAMFS mount and enters the ordinary stage-1 supervisor;
formal executables are not mapped from the original archive. This exercises the
root transition, service handoff and application contracts without requiring a
physical block provider on every architecture. It does not validate GPT, EROFS,
storage-provider replacement, or product boot latency and memory consumption.
The separate `board-storage` suite verifies the real independent disk root and
bootstrap retirement. The synthetic fixture is not a supported product layout;
ordinary board archives contain static stage-1 programs only.
The independent `native-stack-16k` shard runs this acceptance with the minimum
supported kernel thread stack on one and four CPUs. It requires the boot log to
confirm 16 KiB stacks and the shell to finish the application probes; reaching
kernel startup alone is insufficient. IRQ and emergency stacks retain their
defaults. Run `sh tests/ci/run.sh native-stack-16k` locally; its generated config
does not modify `kernel/.config`. CI retains the config and both serial logs.
The RISC-V Native image includes userspace VM provisioning. Its Native suite
checks Linux guest startup on one and four host harts and runtime-crash recovery;
this does not imply multi-vCPU RISC-V guest support.

Native acceptance reports each phase transition with elapsed times. Each phase
has a 90-second progress deadline (`QEMU_BOOT_TIMEOUT_SECONDS`), and the complete
multi-command run has a 300-second deadline (`QEMU_NATIVE_TIMEOUT_SECONDS`).
The workflow retains the raw Native console log alongside runtime-crash logs,
including on failure, so a stalled phase can be distinguished from exhaustion
of the overall test budget.
The separate guest SMP scenario allows a fixed 300 seconds for each guest boot
or reboot on shared TCG runners; ordinary commands retain a 90-second deadline.
On failure it prints the raw serial tail, including filtered guest boot records.
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

`make test-boot-system ARCH=aarch64` reproduces the split-root and recovery
part of `board-storage`. It creates an isolated disk, checks supervision handoff,
the final namespace, shell/tool capabilities, VM start/stop and writable/read-only
unmount/remount behavior, then boots the same archive without storage controllers
and requires the static recovery shell to remain usable. A third boot uses
a copied bootstrap policy selecting an exiting init candidate; it verifies
namespace rollback, live storage services, and static recovery mount operations.
Two further boots use separate system-image copies missing the formal I/O runtime
or EROFS worker. They require failed replacement preparation to retain the original
providers and `/boot`, keep the formal shell available, and serve mount, write,
unmount and remount requests through the retained filesystem manager.
The fallback shell does not receive a formal loader or formal shell delegation;
the recovery cases do not launch a system shell through it. Separate synthetic
storage-failure acceptance includes formal-shell failure after handoff, which
must not reopen bootstrap recovery.
The disk and all serial logs stay under `target/board-tests/boot-system.*/`;
this test never modifies the developer's deployment disk.

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
