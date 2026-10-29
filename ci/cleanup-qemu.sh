#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Last-resort runner cleanup; normal test scripts already reap their QEMU child.
set -eu

case "${1:-}" in
    aarch64) process=qemu-system-aarch64 ;;
    riscv64) process=qemu-system-riscv64 ;;
    *)
        echo "usage: tests/ci/cleanup-qemu.sh {aarch64|riscv64}" >&2
        exit 2
        ;;
esac

pkill -TERM -x "$process" 2>/dev/null || true
attempt=0
while [ "$attempt" -lt 5 ]; do
    if ! pgrep -x "$process" >/dev/null 2>&1; then
        exit 0
    fi
    attempt=$((attempt + 1))
    sleep 1
done
pkill -KILL -x "$process" 2>/dev/null || true
