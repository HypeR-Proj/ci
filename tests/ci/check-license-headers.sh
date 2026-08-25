#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Require project-authored text files to carry machine-readable license data.
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)
cd "$root"

missing=$(mktemp "${TMPDIR:-/tmp}/hyper-license-headers.XXXXXX")
trap 'rm -f "$missing"' EXIT HUP INT TERM

git ls-files --cached --others --exclude-standard | sort -u | while IFS= read -r path; do
    # A worktree move appears as a tracked deletion plus an untracked addition
    # before staging. Inspect the files that actually form the candidate tree.
    [ -f "$path" ] || continue
    case "$path" in
        LICENSE | Cargo.lock | */Cargo.lock)
            # LICENSE is the license text; Cargo owns generated lockfiles.
            continue
            ;;
    esac

    header=$(sed -n '1,8p' "$path")
    if ! printf '%s\n' "$header" |
        grep -Fq 'SPDX-FileCopyrightText: 2026 roolrz' ||
        ! printf '%s\n' "$header" |
            grep -Fq 'SPDX-License-Identifier: Apache-2.0'; then
        printf '%s\n' "$path" >>"$missing"
    fi
done

if [ -s "$missing" ]; then
    echo "tracked files missing the HypeR SPDX header:" >&2
    cat "$missing" >&2
    exit 1
fi
