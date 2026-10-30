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
for directory, notice, expected in (
    ('stdlib/musl', 'COPYRIGHT', 'f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af'),
    ('stdlib/cxx', 'LICENSE.TXT', '539dd7aed86e8a4f12cbdd0e6c50c189c7d74847e4fecc64ce2c6ee3a01da38b'),
):
    library = Path(directory)
    if library.exists():
        if hashlib.sha256((library / notice).read_bytes()).hexdigest() != expected:
            raise SystemExit(f'{directory}: upstream license changed; review provenance')
        if (library / 'LICENSE').read_bytes() != (library / notice).read_bytes():
            raise SystemExit(f'{directory}: public license differs from preserved upstream notice')
        if not (library / 'UPSTREAM.json').is_file():
            raise SystemExit(f'{directory}: missing source provenance')
vendor = Path('third_party/rust-fatfs')
if vendor.exists():
    expected = '9125b4be91e0486ca97316a7547ec0f7e15093b3eacbf4d85e4de1718e9bbfbf'
    if hashlib.sha256((vendor / 'LICENSE.txt').read_bytes()).hexdigest() != expected:
        raise SystemExit('rust-fatfs upstream MIT notice changed; review its provenance')
    if not (vendor / 'PROVENANCE.md').is_file():
        raise SystemExit('rust-fatfs requires an upstream revision and patch provenance record')
PY

{
    if [ -f project.py ]; then
        python3 project.py files
    else
        git ls-files --cached --others --exclude-standard | sort -u
    fi
} | while IFS= read -r path; do
    # A worktree move appears as a tracked deletion plus an untracked addition
    # before staging. Inspect the files that actually form the candidate tree.
    [ -f "$path" ] || continue
    case "$path" in
        stdlib/musl/hyper/* | stdlib/cxx/hyper/* | \
        stdlib/musl/.github/* | stdlib/cxx/.github/* | \
        stdlib/musl/README.md | stdlib/cxx/README.md | \
        stdlib/musl/CONTRIBUTING.md | stdlib/cxx/CONTRIBUTING.md | \
        stdlib/musl/SECURITY.md | stdlib/cxx/SECURITY.md | \
        stdlib/musl/Kconfig | stdlib/cxx/Kconfig | \
        stdlib/musl/component.json | stdlib/cxx/component.json | \
        stdlib/musl/component.mk | stdlib/cxx/component.mk | \
        stdlib/musl/UPSTREAM.json | stdlib/cxx/UPSTREAM.json | \
        stdlib/musl/.gitignore | stdlib/cxx/.gitignore)
            # New Native integration retains the normal project header policy.
            ;;
        stdlib/musl/* | stdlib/cxx/*)
            # Imported forks retain their upstream notices, verified above.
            continue
            ;;
        third_party/rust-fatfs/src/* | third_party/rust-fatfs/Cargo.toml | \
        third_party/rust-fatfs/README.md | third_party/rust-fatfs/LICENSE.txt)
            continue
            ;;
        io-vm/* | pi5-images/* | tests/legacy/*)
            # Independently licensed publishers and preserved upstream workflows.
            continue
            ;;
        LICENSE | */LICENSE | */LICENSE.txt | */LICENSE-HypeR | Cargo.lock | */Cargo.lock)
            # LICENSE is the license text; Cargo owns generated lockfiles.
            continue
            ;;
    esac

    # Binary artwork and filesystem fixtures keep SPDX metadata in a sidecar;
    # do not parse their bytes as shell strings or exempt attribution checks.
    metadata=$path
    case "$path" in
        *.png | *.jpg | *.jpeg | *.gif | *.webp | *.ico | *.erofs)
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
