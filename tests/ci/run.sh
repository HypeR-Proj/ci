#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Stable repository-level entry points used by GitHub Actions.
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)
cd "$root"

usage() {
    echo "usage: tests/ci/run.sh {quality|scripts|native|io-vm|riscv64-native|aarch64-build|aarch64-qemu|riscv64-qemu|x86_64-build}" >&2
    exit 2
}

run_kernel_suite() {
    suite=$1
    (cd kernel && sh tests/ci/run.sh "$suite")
}

case "${1:-}" in
    quality)
        command -v rg >/dev/null 2>&1 || {
            echo "ripgrep is required for the source-quality suite" >&2
            exit 2
        }
        sh tests/ci/check-monorepo-contract.sh
        sh tests/ci/check-license-headers.sh
        run_kernel_suite quality
        ;;
    scripts)
        python3 tests/build/incremental.py
        python3 -B tests/build/io-vm-run.py
        python3 -B tests/build/io-vm-package.py
        python3 -B tests/qemu/test-guest-smp.py
        python3 -B tests/qemu/test-io-vm.py
        command -v shellcheck >/dev/null 2>&1 || {
            echo "shellcheck is required for the script-quality suite" >&2
            exit 2
        }
        find tests kernel/tests kernel/tools scripts sdk/toolchain -type f \
            \( -name '*.sh' -o -name hyper-clang -o -name hyper-cargo \) -print0 |
            xargs -0 shellcheck --severity=warning
        ;;
    native)
        make sdk-check
        make sdk-test
        make app-check
        make app-test
        QEMU_TEST_LOG=target/app/aarch64/native-init.log \
            make test-native ARCH=aarch64 QEMU_CPU=max QEMU_CPUS=4
        make -o image -o native-initramfs test-console ARCH=aarch64
        make -o image -o native-initramfs test-apps ARCH=aarch64
        make -o image -o native-initramfs test-runtime-crash ARCH=aarch64
        make -o image test-vm-smoke ARCH=aarch64 QEMU_CPUS=4
        make -o image test-vm-smoke ARCH=aarch64 QEMU_CPUS=1 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2
        cp target/app/aarch64/console.log target/app/aarch64/native-gicv3-console.log
        cp target/app/aarch64/runtime-crash.log target/app/aarch64/native-gicv3-runtime-crash.log
        QEMU_TEST_LOG=target/app/aarch64/native-gicv2-smp.log \
            make test-native-gicv2 QEMU_CPUS=4
        make -o image -o native-initramfs test-console test-runtime-crash ARCH=aarch64 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2 QEMU_CPUS=4 \
            NATIVE_INITRAMFS="$root/target/app/aarch64/initramfs-gicv2.cpio"
        cp target/app/aarch64/console.log target/app/aarch64/native-gicv2-console.log
        cp target/app/aarch64/runtime-crash.log target/app/aarch64/native-gicv2-runtime-crash.log
        QEMU_TEST_LOG=target/app/aarch64/native-gicv2-up.log \
            make -o image -o native-initramfs test-native-gicv2 QEMU_CPUS=1
        make -o image test-guest-smp ARCH=aarch64 QEMU_CPUS=4
        cp target/app/aarch64/guest-smp.log target/app/aarch64/native-gicv3-guest-smp.log
        make -o image -o guest-smp-initramfs test-guest-smp ARCH=aarch64 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2 QEMU_CPUS=4
        cp target/app/aarch64/guest-smp.log target/app/aarch64/native-gicv2-guest-smp.log
        make -o image -o guest-smp-initramfs test-guest-smp ARCH=aarch64 QEMU_CPUS=1
        cp target/app/aarch64/guest-smp.log target/app/aarch64/native-gicv3-guest-smp-overcommit.log
        # Reuse the abrupt runtime-exit fixture with the SMP topology. It may
        # exit before every secondary has booted; retirement must include all
        # configured dormant and running CPU Threads in either case.
        make -o image -o native-initramfs test-runtime-crash ARCH=aarch64 \
            QEMU_MACHINE=virt,virtualization=on,gic-version=2 QEMU_CPUS=4 \
            NATIVE_GUEST_VCPUS=4 \
            NATIVE_GUEST_ITB="$root/kernel/target/guest/aarch64/alpine-smp.itb"
        cp target/app/aarch64/runtime-crash.log target/app/aarch64/native-gicv2-guest-smp-runtime-crash.log
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
    riscv64-native)
        make sdk-check ARCH=riscv64
        make app-check ARCH=riscv64
        QEMU_TEST_LOG=target/app/riscv64/native-init-smp.log \
            make test-native ARCH=riscv64 QEMU_CPUS=4
        QEMU_TEST_LOG=target/app/riscv64/native-init-up.log \
            make -o image -o native-initramfs test-native ARCH=riscv64 QEMU_CPUS=1
        make -o image -o native-initramfs test-console ARCH=riscv64
        make -o image -o native-initramfs test-apps ARCH=riscv64
        make -o image test-vm-smoke ARCH=riscv64 QEMU_CPUS=4
        make -o image test-vm-smoke ARCH=riscv64 QEMU_CPUS=1
        make -o image -o native-initramfs test-runtime-crash ARCH=riscv64
        ;;
    aarch64-build | aarch64-qemu | riscv64-qemu | x86_64-build)
        run_kernel_suite "$1"
        ;;
    *)
        usage
        ;;
esac
