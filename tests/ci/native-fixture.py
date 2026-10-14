#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Publish/check immutable Native test images from the current checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


TARGETS = {
    'aarch64': 'aarch64-unknown-none-softfloat',
    'riscv64': 'riscv64imac-unknown-none-elf',
}
FILES = ('hyper.img', 'initramfs.cpio')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def publish(root, directory, arch, revision):
    directory.mkdir(parents=True, exist_ok=True)
    sources = (
        root / 'kernel/target' / TARGETS[arch] / 'kernel/hyper.img',
        root / 'target/app' / arch / 'initramfs.cpio',
    )
    for name, source in zip(FILES, sources):
        shutil.copyfile(source, directory / name)
    manifest = {
        'version': 1, 'arch': arch, 'revision': revision,
        'files': {name: digest(directory / name) for name in FILES},
    }
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def verify(directory, arch, revision):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if (manifest.get('version') != 1 or manifest.get('arch') != arch
            or manifest.get('revision') != revision):
        raise ValueError('Native fixture does not match this revision and architecture')
    if set(manifest.get('files', {})) != set(FILES):
        raise ValueError('Native fixture has an unexpected file set')
    for name in FILES:
        path = directory / name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError(f'Native fixture is missing a regular nonempty {name}')
        if digest(path) != manifest['files'][name]:
            raise ValueError(f'Native fixture checksum mismatch: {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pack', 'check'))
    parser.add_argument('--arch', required=True, choices=TARGETS)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if args.action == 'pack':
        publish(root, args.directory, args.arch, revision)
    verify(args.directory, args.arch, revision)


if __name__ == '__main__':
    main()
