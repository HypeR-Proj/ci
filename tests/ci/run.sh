#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Stable repository-level entry points used by GitHub Actions.
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)
cd "$root"

usage() {
    echo "usage: tests/ci/run.sh {quality|scripts|native|aarch64-build|aarch64-qemu|riscv64-qemu|x86_64-build}" >&2
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
        make test-native ARCH=aarch64 QEMU_CPU=cortex-a72 QEMU_CPUS=4
        ;;
    aarch64-build | aarch64-qemu | riscv64-qemu | x86_64-build)
        run_kernel_suite "$1"
        ;;
    *)
        usage
        ;;
esac
