#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Require project-authored text files to carry machine-readable license data.
set -eu

root=${HYPER_LICENSE_ROOT:-$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)}
cd "$root"

missing=$(mktemp "${TMPDIR:-/tmp}/hyper-license-headers.XXXXXX")
trap 'rm -f "$missing"' EXIT HUP INT TERM

# This narrowly scoped dependency retains its upstream MIT copyright; it is
# not project-authored Apache source. Pin the notice rather than rewriting it.
python3 - <<'PY'
import hashlib
from pathlib import Path
vendor = Path('third_party/rust-fatfs')
if vendor.exists():
    expected = '9125b4be91e0486ca97316a7547ec0f7e15093b3eacbf4d85e4de1718e9bbfbf'
    if hashlib.sha256((vendor / 'LICENSE.txt').read_bytes()).hexdigest() != expected:
        raise SystemExit('rust-fatfs upstream MIT notice changed; review its provenance')
    if not (vendor / 'PROVENANCE.md').is_file():
        raise SystemExit('rust-fatfs requires an upstream revision and patch provenance record')
PY

git ls-files --cached --others --exclude-standard | sort -u | while IFS= read -r path; do
    # A worktree move appears as a tracked deletion plus an untracked addition
    # before staging. Inspect the files that actually form the candidate tree.
    [ -f "$path" ] || continue
    case "$path" in
        third_party/rust-fatfs/src/* | third_party/rust-fatfs/Cargo.toml | \
        third_party/rust-fatfs/README.md | third_party/rust-fatfs/LICENSE.txt)
            continue
            ;;
        LICENSE | Cargo.lock | */Cargo.lock)
            # LICENSE is the license text; Cargo owns generated lockfiles.
            continue
            ;;
    esac

    # Binary artwork keeps SPDX metadata in a text sidecar; do not parse image
    # bytes as shell strings or exempt the artwork from attribution checks.
    metadata=$path
    case "$path" in
        *.png | *.jpg | *.jpeg | *.gif | *.webp | *.ico)
            metadata=$path.license
            if [ ! -f "$metadata" ]; then
                printf '%s\n' "$path (missing .license sidecar)" >>"$missing"
                continue
            fi
            ;;
    esac
    header=$(sed -n '1,8p' "$metadata")
    case "$path" in
        *.json)
            copyright='"SPDX-FileCopyrightText"[[:space:]]*:[[:space:]]*"[0-9]{4}(-[0-9]{4})?[[:space:]]+[^"[:space:]][^"]*"'
            license='"SPDX-License-Identifier": "Apache-2.0"'
            ;;
        *)
            copyright='SPDX-FileCopyrightText:[[:space:]]+[0-9]{4}(-[0-9]{4})?[[:space:]]+[^[:space:]*/#]'
            license='SPDX-License-Identifier: Apache-2.0'
            ;;
    esac
    if ! printf '%s\n' "$header" | grep -Eq "$copyright" ||
        ! printf '%s\n' "$header" | grep -Fq "$license"; then
        printf '%s\n' "$path" >>"$missing"
    fi
done

if [ -s "$missing" ]; then
    echo "tracked files missing the HypeR SPDX header:" >&2
    cat "$missing" >&2
    exit 1
fi
