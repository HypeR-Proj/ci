#!/bin/sh
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

# Check attribution acceptance without modifying the developer's working tree.
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)
fixture=$(mktemp -d "${TMPDIR:-/tmp}/hyper-license-test.XXXXXX")
trap 'rm -rf "$fixture"' EXIT HUP INT TERM
git -C "$fixture" init -q
check() {
    HYPER_LICENSE_ROOT="$fixture" sh "$root/tests/ci/check-license-headers.sh"
}
for author in '2026 Contributor Name' '2027 Another Contributor' '2024-2027 Project Authors'; do
    printf '// SPDX-FileCopyrightText: %s\n// SPDX-License-Identifier: Apache-2.0\n' "$author" >"$fixture/test.rs"
    printf '{"SPDX-FileCopyrightText": "%s", "SPDX-License-Identifier": "Apache-2.0"}\n' "$author" >"$fixture/test.json"
    check
done
for author in 'Contributor Name' '2026 ' '2026 */'; do
    printf '// SPDX-FileCopyrightText: %s\n// SPDX-License-Identifier: Apache-2.0\n' "$author" >"$fixture/test.rs"
    if check >/dev/null 2>&1; then
        echo "invalid copyright accepted: $author" >&2
        exit 1
    fi
done
rm "$fixture/test.rs"
printf '{"SPDX-FileCopyrightText": "2026 ", "SPDX-License-Identifier": "Apache-2.0"}\n' >"$fixture/test.json"
if check >/dev/null 2>&1; then
    echo 'empty JSON attribution accepted' >&2
    exit 1
fi
rm "$fixture/test.json"
printf '// SPDX-FileCopyrightText: 2026 Contributor\n' >"$fixture/test.rs"
if check >/dev/null 2>&1; then
    echo 'missing license accepted' >&2
    exit 1
fi
rm "$fixture/test.rs"
printf 'generated lockfile\n' >"$fixture/Cargo.lock"
check
mkdir -p "$fixture/third_party/rust-fatfs"
printf 'wrong upstream notice\n' >"$fixture/third_party/rust-fatfs/LICENSE.txt"
if check >/dev/null 2>&1; then
    echo 'changed upstream notice accepted' >&2
    exit 1
fi
