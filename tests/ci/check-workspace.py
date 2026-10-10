#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate in-tree ABI ownership and app dependency declarations as TOML."""

import argparse
from pathlib import Path
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from cargo_workspace import member_directories


def read_manifest(path):
    with path.open('rb') as stream:
        return tomllib.load(stream)


def dependencies(manifest):
    contexts = [manifest, manifest.get('workspace', {}),
                *manifest.get('target', {}).values()]
    for context in contexts:
        for section in ('dependencies', 'dev-dependencies', 'build-dependencies'):
            yield from context.get(section, {}).items()
    for patch in manifest.get('patch', {}).values():
        yield from patch.items()


def package_name(alias, declaration):
    return declaration.get('package', alias) if isinstance(declaration, dict) else alias


def check_abi(root):
    manifest_path = root / 'kernel/core/Cargo.toml'
    declarations = read_manifest(manifest_path).get('dependencies', {})
    matches = [spec for alias, spec in declarations.items()
               if package_name(alias, spec) == 'hyper-abi']
    if (len(matches) != 1 or not isinstance(matches[0], dict)
            or 'path' not in matches[0]
            or (manifest_path.parent / matches[0]['path']).resolve() != root / 'sdk/abi'):
        raise ValueError('kernel core must consume the in-tree hyper-abi crate')


def check_apps(root):
    app = root / 'app'
    workspace_manifest = read_manifest(app / 'Cargo.toml')
    members = {}
    for directory in sorted(member_directories(app / 'Cargo.toml')):
        if not any(directory.is_relative_to(base) for base in (app, root / 'lib')):
            raise ValueError(f'app workspace member escapes app/ or lib/: {directory}')
        members[directory] = read_manifest(directory / 'Cargo.toml')
    for directory, manifest in [(app, workspace_manifest), *members.items()]:
        for alias, spec in dependencies(manifest):
            name = package_name(alias, spec)
            if name == 'hyper-sys':
                raise ValueError(f'{directory}/Cargo.toml: {alias} exposes raw syscalls; use hyper-os')
            if not isinstance(spec, dict) or 'path' not in spec:
                continue
            target = (directory / spec['path']).resolve()
            if target not in members or members[target]['package']['name'] != name:
                raise ValueError(f'{directory}/Cargo.toml: {alias} must use the installed SDK '
                                 'or an app workspace member')


def check_index(index):
    for record in index.split(b'\0'):
        if not record:
            continue
        metadata, path = record.split(b'\t', 1)
        if metadata.split()[0] == b'160000':
            raise ValueError('the source tree must not contain Git submodules')
        if path.rsplit(b'/', 1)[-1] == b'components.lock':
            raise ValueError('in-tree components must not have cross-repository revision locks')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    root = parser.parse_args().root.resolve()
    check_abi(root)
    check_apps(root)
    index = subprocess.run(['git', 'ls-files', '--stage', '-z'], cwd=root,
                           check=True, capture_output=True).stdout
    check_index(index)
    print('workspace dependency declarations verified')


if __name__ == '__main__':
    main()
