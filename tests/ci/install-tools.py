#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Install missing CI tools, with bounded downloads and reusable Debian archives."""

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess

# Hosted runners already carry several of these tools outside dpkg. Installing
# the matching Ubuntu metapackage needlessly downloads another copy of CMake.
COMMANDS = {
    'clang': ('clang',), 'cmake': ('cmake',), 'cpio': ('cpio',),
    'lld': ('ld.lld',), 'llvm': ('llvm-ar', 'llvm-ranlib', 'llvm-readobj'),
    'make': ('make',), 'shellcheck': ('shellcheck',), 'ripgrep': ('rg',),
    'qemu-system-arm': ('qemu-system-aarch64',),
    'qemu-system-misc': ('qemu-system-riscv64',),
    'binutils-aarch64-linux-gnu': ('aarch64-linux-gnu-nm',),
    'dosfstools': ('mkfs.fat',), 'mtools': ('mcopy',),
    'e2fsprogs': ('mke2fs',), 'squashfs-tools': ('mksquashfs', 'unsquashfs'),
    'device-tree-compiler': ('dtc',), 'doxygen': ('doxygen',),
}


def missing_packages(packages):
    unknown = set(packages) - COMMANDS.keys()
    if unknown:
        raise ValueError(f'unknown CI tool packages: {sorted(unknown)}')
    return sorted({package for package in packages
                   if not all(shutil.which(command) for command in COMMANDS[package])})


def install(packages, cache):
    missing = missing_packages(packages)
    if not missing:
        print('All requested CI tools are already available', flush=True)
        return
    release = platform.freedesktop_os_release()
    if (release.get('ID'), release.get('VERSION_ID')) != ('ubuntu', '24.04'):
        raise ValueError('CI tool installation requires Ubuntu 24.04')
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    sources = cache.parent / 'hyper-tools.list'
    source_parts = cache.parent / 'hyper-tools.sources.d'
    source_parts.mkdir(exist_ok=True)
    options = [
        '-o', f'Dir::Cache::archives={cache}',
        '-o', f'Dir::Etc::sourcelist={sources}',
        '-o', f'Dir::Etc::sourceparts={source_parts}',
        '-o', 'Acquire::Retries=1',
        '-o', 'Acquire::http::Timeout=15',
    ]
    # Only downloads have a wall-clock deadline: interrupting dpkg halfway
    # through an installation would leave a partially configured runner.
    for archive, security in (
        ('http://archive.ubuntu.com/ubuntu', 'http://security.ubuntu.com/ubuntu'),
        ('http://azure.archive.ubuntu.com/ubuntu', 'http://azure.archive.ubuntu.com/ubuntu'),
    ):
        sources.write_text(''.join(
            f'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] '
            f'{mirror} {suite} main universe restricted multiverse\n'
            for mirror, suite in ((archive, 'noble'), (archive, 'noble-updates'),
                                  (security, 'noble-security'))
        ))
        print(f'Installing missing tools {missing} via {archive}', flush=True)
        update = ['sudo', 'timeout', '--kill-after=10s', '60s',
                  'apt-get', *options, 'update', '--error-on=any']
        download = ['sudo', 'timeout', '--kill-after=10s', '180s',
                    'apt-get', *options, '--assume-yes', '--no-install-recommends',
                    '--download-only', 'install', *missing]
        if subprocess.run(update).returncode == 0 and subprocess.run(download).returncode == 0:
            break
    else:
        raise RuntimeError('CI tool downloads failed on both Ubuntu mirrors')
    # APT selects packages using freshly authenticated indexes and verifies
    # archive hashes, including when the .deb files came from Actions cache.
    subprocess.run(['sudo', 'apt-get', *options, '--assume-yes',
                    '--no-install-recommends', '--no-download', 'install', *missing], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('packages', nargs='+', choices=COMMANDS)
    args = parser.parse_args()
    install(args.packages, args.cache)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'cacheable={str(any(args.cache.glob("*.deb"))).lower()}\n')


if __name__ == '__main__':
    main()
