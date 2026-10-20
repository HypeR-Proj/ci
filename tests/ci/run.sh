#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Stable repository-level entry points used by GitHub Actions.
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)
cd "$root"

usage() {
    echo "usage: tests/ci/run.sh SUITE (see tests/ci/README.md for suites and aggregates)" >&2
    exit 2
}

run_kernel_suite() {
    suite=$1
    (cd kernel && sh tests/ci/run.sh "$suite")
}

prepare_aarch64_stack16_config() {
    mkdir -p target/ci
    stack_config="$root/target/ci/aarch64-stack16.config"
    sed 's/^CONFIG_KERNEL_STACK_SIZE_KB=.*/CONFIG_KERNEL_STACK_SIZE_KB=16/' \
        kernel/configs/qemu_aarch64_defconfig >"$stack_config"
}

# Only the ordinary boot/interactive probes share immutable images. Feature,
# topology and failure fixtures keep their own builds and mutable disks.
run_native_probe() {
    arch=${CI_ARCH:-aarch64}
    case "$arch" in aarch64|riscv64) ;; *) usage ;; esac
    if [ -n "${CI_NATIVE_FIXTURE:-}" ]; then
        python3 -B tests/ci/native-fixture.py check --arch "$arch" \
            --directory "$CI_NATIVE_FIXTURE"
        mkdir -p "target/app/$arch"
        make -o image -o native-initramfs "$@" ARCH="$arch" \
            KERNEL_IMAGE="$CI_NATIVE_FIXTURE/hyper.img" \
            NATIVE_INITRAMFS="$CI_NATIVE_FIXTURE/initramfs.cpio"
    else
        make "$@" ARCH="$arch"
    fi
}

case "${1:-}" in
    quality)
        command -v rg >/dev/null 2>&1 || {
            echo "ripgrep is required for the source-quality suite" >&2
            exit 2
        }
        python3 -B tests/ci/test-workspace.py
        python3 -B tests/ci/check-workspace.py
        sh tests/ci/check-license-headers.sh
        sh tests/ci/test-license-headers.sh
        run_kernel_suite quality
        ;;
    scripts)
        python3 -B kernel/tests/ci/test-features.py
        python3 -B svc/terminal-service/tests/linux-formatting.py
        python3 -B tests/ci/test-install-tools.py
        python3 -B tests/ci/test-native-fixture.py
        python3 -B tests/ci/test-suites.py
        python3 -B tests/profiling/test-flamegraph.py
        python3 -B tests/build/kernel-version.py
        python3 -B tests/build/developer-entrypoints.py
        python3 tests/build/incremental.py
        python3 -B tests/build/app-deployment.py
        python3 -B tests/build/system-config.py
        python3 -B tests/build/native-libraries.py
        python3 -B tests/build/io-vm-run.py
        python3 -B tests/build/io-vm-package.py
        python3 -B tests/build/board-image.py
        python3 -B tests/build/board-image-update.py
        python3 -B tests/build/fetch-rpi5-boot.py
        python3 -B tests/build/rpi5-bringup.py
        python3 -B tests/build/guest-rootfs.py
        python3 -B tests/qemu/test-guest-smp.py
        python3 -B tests/qemu/test-io-vm.py
        python3 -B tests/qemu/test-stack.py
        python3 -B tests/qemu/test-boot-system.py
        python3 -B tests/qemu/test-product-config.py
        python3 -B tests/qemu/test-session.py
        python3 -B tests/qemu/test-lifecycle-stress.py
        python3 -B tests/qemu/test-power-crash.py
        python3 -B tests/qemu/test-command.py
        python3 -B tests/qemu/test-console.py
        python3 -B tests/qemu/test-console-hotplug.py
        python3 -B tests/qemu/test-fleet-config.py
        python3 -B tests/qemu/test-network.py
        command -v shellcheck >/dev/null 2>&1 || {
            echo "shellcheck is required for the script-quality suite" >&2
            exit 2
        }
        find tests kernel/tests kernel/tools scripts sdk/toolchain -type f \
            \( -name '*.sh' -o -name hyper-clang -o -name hyper-cargo \) -print0 |
            xargs -0 shellcheck --severity=warning
        ;;
    kernel-configurations)
        python3 -B kernel/tests/ci/check-features.py
        ;;
    native)
        # Separate processes keep dispatch state local to each suite. Local
        # runs remain serial because fixtures reuse the same output paths.
        for shard in native-sdk native-gicv3 native-gicv2 native-smp-stack native-stack-16k native-pmu; do
            sh tests/ci/run.sh "$shard"
        done
        ;;
    native-sdk)
        make sdk-check
        make sdk-test
        make app-check
        make app-test
        python3 -B tests/build/service-manifest.py
        make app-sdk-test
        ;;
    native-fixture)
        arch=${CI_ARCH:-aarch64}
        case "$arch" in aarch64|riscv64) ;; *) usage ;; esac
        make image native-initramfs ARCH="$arch"
        python3 -B tests/ci/native-fixture.py pack --arch "$arch" \
            --directory "target/ci/native-$arch"
        ;;
    native-gicv3)
        for shard in native-boot native-console native-terminal native-apps native-shell native-stress \
            native-fleet native-services native-storage-recovery native-filesystem native-runtime-crash native-vm; do
            sh tests/ci/run.sh "$shard"
        done
        ;;
    native-boot)
        arch=${CI_ARCH:-aarch64}
        for cpus in 4 1; do
            QEMU_TEST_LOG="target/app/$arch/native-init-$cpus.log" \
                run_native_probe test-native QEMU_CPUS="$cpus"
        done
        ;;
    native-apps)
        run_native_probe test-apps test-editor test-less
        ;;
    native-pmu)
        make test-pmu ARCH=aarch64
        QEMU_MACHINE=virt,virtualization=on,gic-version=2 \
            make -o image -o native-initramfs test-pmu ARCH=aarch64 \
                PMU_LOG=target/app/aarch64/pmu-gicv2.log
        QEMU_CPU=max,pmu=off python3 -B tests/qemu/verify-pmu.py qemu-system-aarch64 \
            kernel/target/aarch64-unknown-none-softfloat/kernel/hyper.img \
            target/app/aarch64/initramfs.cpio target/app/aarch64/pmu-unavailable.log \
            --kernel kernel/target/aarch64-unknown-none-softfloat/kernel/hyper \
            --nm "$(sh scripts/find-llvm-tool.sh llvm-nm)" --unsupported
        ;;
    native-stress)
        for cpus in 1 4; do
            run_native_probe test-lifecycle-stress QEMU_CPUS="$cpus"
        done
        ;;
    native-console|native-terminal|native-shell)
        run_native_probe "test-${1#native-}"
        ;;
    native-services)
        make test-services ARCH=aarch64
        make test-process-retirement ARCH=aarch64
        make test-service-failure ARCH=aarch64
        ;;
    native-fleet)
        make image native-initramfs ARCH=aarch64
        make -o image -o app test-fleet-config ARCH=aarch64
        ;;
    native-storage-recovery)
        make image native-initramfs ARCH=aarch64
        make -o image -o app test-storage-failure ARCH=aarch64
        ;;
    native-filesystem)
        make test-filesystem-failure ARCH=aarch64 QEMU_CPU=max,cntfrq=54000000
        ;;
    native-runtime-crash)
        make test-runtime-crash ARCH=aarch64
        ;;
    native-vm)
        make test-vm-smoke ARCH=aarch64 QEMU_CPUS=4
        make -o image test-vm-smoke ARCH=aarch64 QEMU_CPUS=1 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2
        # The TC-test image forces the GICv3 common-register trap even on
        # QEMU CPUs with TDIR, covering the compatibility DIR/PMR/CTLR/RPR path.
        make -C kernel image ARCH=aarch64 CARGO_FEATURES='--features kernel-test,kernel-vgic-tc-test'
        make -o image test-vm-smoke ARCH=aarch64 QEMU_CPUS=4
        cp target/app/aarch64/vm-smoke-4.log target/app/aarch64/native-gicv3-tc.log
        ;;
    native-gicv2)
        for shard in native-boot native-console native-runtime-crash; do
            QEMU_MACHINE=virt,virtualization=on,gic-version=2,dtb-randomness=on \
                sh tests/ci/run.sh "$shard"
        done
        ;;
    native-smp-stack)
        for shard in native-smp-gicv3 native-smp-gicv2 native-smp-overcommit native-retirement-stack; do
            sh tests/ci/run.sh "$shard"
        done
        ;;
    native-smp-gicv3)
        make test-guest-smp ARCH=aarch64 QEMU_CPUS=4
        cp target/app/aarch64/guest-smp.log target/app/aarch64/native-gicv3-guest-smp.log
        ;;
    native-smp-gicv2)
        sh tests/ci/run.sh native-smp-gicv2-guest
        sh tests/ci/run.sh native-smp-gicv2-retirement
        ;;
    native-smp-gicv2-guest)
        make test-guest-smp ARCH=aarch64 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2 QEMU_CPUS=4
        cp target/app/aarch64/guest-smp.log target/app/aarch64/native-gicv2-guest-smp.log
        ;;
    native-smp-gicv2-retirement)
        make image guest-smp-initramfs ARCH=aarch64
        # Reuse the abrupt runtime-exit fixture with the SMP topology. It may
        # exit before every secondary has booted; retirement must include all
        # configured dormant and running CPU Threads in either case.
        make -o image -o native-initramfs test-runtime-crash ARCH=aarch64 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2 QEMU_CPUS=4 \
            NATIVE_VM_CONFIG="$root/bootstrap/stage1/init/tests/config/vms-smp-4.json" \
            NATIVE_GUEST_ITB="$root/kernel/target/guest/aarch64/alpine-smp.itb"
        cp target/app/aarch64/runtime-crash.log target/app/aarch64/native-gicv2-guest-smp-runtime-crash.log
        ;;
    native-smp-overcommit)
        make test-guest-smp ARCH=aarch64 QEMU_CPUS=1
        cp target/app/aarch64/guest-smp.log target/app/aarch64/native-gicv3-guest-smp-overcommit.log
        ;;
    native-retirement-stack)
        make test-power-crash ARCH=aarch64 QEMU_CPUS=4
        make test-stack ARCH=aarch64
        ;;
    native-stack-16k)
        prepare_aarch64_stack16_config
        make image native-initramfs ARCH=aarch64 CONFIG_FILE="$stack_config" CARGO_FEATURES=
        for cpus in 1 4; do
            log="target/app/aarch64/native-stack-16k-${cpus}.log"
            QEMU_TEST_LOG="$log" make -o image -o native-initramfs test-native \
                ARCH=aarch64 CONFIG_FILE="$stack_config" CARGO_FEATURES= \
                QEMU_CPU=max QEMU_CPUS="$cpus"
            if ! grep -Fq 'guarded kernel stacks: thread 16 KiB,' "$log"; then
                echo "Native boot did not report 16 KiB kernel thread stacks: $log" >&2
                exit 1
            fi
        done
        ;;
    io-vm)
        package=$(python3 -B scripts/fetch-io-vm.py \
            --reference "${IO_VM_REFERENCE:-}" --platform qemu)
        make test-io-vm ARCH=aarch64 IO_VM_PACKAGE="$package" \
            IO_VM_TEST=reset QEMU_CPUS=4 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=3
        make -o image -o app-fetch test-io-vm ARCH=aarch64 IO_VM_PACKAGE="$package" \
            IO_VM_TEST=reset QEMU_CPUS=1 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2
        make -o image -o app-fetch test-io-standby ARCH=aarch64 IO_VM_PACKAGE="$package" \
            QEMU_CPUS=4 QEMU_MACHINE=virt,virtualization=on,gic-version=3
        make -o image -o app test-io-standby ARCH=aarch64 IO_VM_PACKAGE="$package" \
            QEMU_CPUS=1 QEMU_MACHINE=virt,virtualization=on,gic-version=2
        ;;
    io-network|io-network-mmio)
        package=$(python3 -B scripts/fetch-io-vm.py \
            --reference "${IO_VM_REFERENCE:-}" --platform qemu)
        target=test-board-network
        if test "$1" = io-network-mmio; then target=test-board-mmio; fi
        make "$target" ARCH=aarch64 IO_VM_PACKAGE="$package"
        ;;
    board-storage)
        for shard in board-storage-media board-boot-system board-alpine-rootfs \
            board-business board-broker board-userspace-device board-product-config; do
            sh tests/ci/run.sh "$shard"
        done
        ;;
    board-product-config)
        package=$(python3 -B scripts/fetch-io-vm.py \
            --reference "${IO_VM_REFERENCE:-}" --platform qemu)
        make test-product-config ARCH=aarch64 IO_VM_PACKAGE="$package" \
            CONFIG_FILE="$root/kernel/configs/qemu_aarch64_defconfig"
        ;;
    board-storage-media|board-boot-system|board-alpine-rootfs|board-business|board-broker|board-userspace-device)
        package=$(python3 -B scripts/fetch-io-vm.py \
            --reference "${IO_VM_REFERENCE:-}" --platform qemu)
        case "$1" in
            board-storage-media) target=test-board-storage ;;
            board-boot-system) target=test-boot-system ;;
            board-alpine-rootfs) target=test-alpine-rootfs ;;
            board-business) target=test-board-business ;;
            board-broker) target=test-board-broker ;;
            board-userspace-device) target=test-userspace-device ;;
        esac
        # Keep the real storage path at the minimum supported thread-stack
        # size, with stack watermark checks, on every independent runner.
        prepare_aarch64_stack16_config
        make "$target" ARCH=aarch64 IO_VM_PACKAGE="$package" \
            CONFIG_FILE="$stack_config" \
            STACK_AUDIT=1 CARGO_FEATURES='--features kernel-test,kernel-stack-audit'
        ;;
    riscv64-native)
        sh tests/ci/run.sh riscv64-sdk
        sh tests/ci/run.sh riscv64-runtime
        ;;
    riscv64-sdk)
        make sdk-check ARCH=riscv64
        make app-check ARCH=riscv64
        make app-test ARCH=riscv64
        make app-sdk-test ARCH=riscv64
        ;;
    riscv64-runtime)
        for shard in native-boot native-console native-terminal native-apps native-shell native-stress; do
            CI_ARCH=riscv64 sh tests/ci/run.sh "$shard"
        done
        sh tests/ci/run.sh riscv64-vm
        ;;
    riscv64-vm)
        make image native-initramfs ARCH=riscv64
        make -o image -o app test-fleet-config ARCH=riscv64
        make -o image test-vm-smoke ARCH=riscv64 QEMU_CPUS=4
        make -o image test-vm-smoke ARCH=riscv64 QEMU_CPUS=1
        make -o image -o native-initramfs test-runtime-crash ARCH=riscv64
        make test-stack ARCH=riscv64
        ;;
    aarch64-build | aarch64-production | aarch64-self-test | aarch64-compact | aarch64-qemu | riscv64-qemu | x86_64-build)
        run_kernel_suite "$1"
        ;;
    *)
        usage
        ;;
esac
