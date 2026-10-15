#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate ABI ownership and bootstrap/system dependency boundaries as TOML."""

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


def check_native_workspace(root, relative, allowed, forbidden=()):
    app = root / relative
    workspace_manifest = read_manifest(app / 'Cargo.toml')
    inherited_dependencies = workspace_manifest.get('workspace', {}).get('dependencies', {})
    members = {}
    for directory in sorted(member_directories(app / 'Cargo.toml')):
        if not any(directory.is_relative_to(root / base) for base in allowed):
            raise ValueError(f'Native workspace member escapes {relative} dependency boundary: {directory}')
        members[directory] = read_manifest(directory / 'Cargo.toml')
    pending = [(app, workspace_manifest), *members.items()]
    visited = set()
    # Walk every dependency context, including optional, build, dev and cfg
    # edges. A currently inactive feature must not bypass the ownership rule.
    # Vendored third-party dependencies are followed rather than trusted leaves.
    while pending:
        directory, manifest = pending.pop()
        if directory in visited:
            continue
        visited.add(directory)
        for alias, spec in dependencies(manifest):
            inherited = isinstance(spec, dict) and spec.get('workspace', False)
            declaration = inherited_dependencies.get(alias, spec) if inherited else spec
            name = package_name(alias, declaration)
            if name in forbidden:
                raise ValueError(f'{directory}/Cargo.toml: {alias} is a system library; '
                                 'bootstrap may only share the stdlib substrate')
            if name == 'hyper-sys':
                raise ValueError(f'{directory}/Cargo.toml: {alias} exposes raw syscalls; use hyper-os')
            if not isinstance(spec, dict) or 'path' not in spec:
                continue
            target = (directory / spec['path']).resolve()
            if target.is_relative_to(root / 'third_party'):
                dependency = read_manifest(target / 'Cargo.toml')
                if dependency['package']['name'] != name:
                    raise ValueError(f'{target}: dependency package name does not match {name}')
                pending.append((target, dependency))
                continue
            if target not in members or members[target]['package']['name'] != name:
                raise ValueError(f'{directory}/Cargo.toml: {alias} must use the installed SDK '
                                 'or a member of its own Native workspace')


def check_apps(root):
    check_native_workspace(root, 'app', ('app', 'svc', 'tests/app', 'lib', 'stdlib/rust-std'))


def check_bootstrap(root):
    check_native_workspace(root, 'bootstrap/stage1', ('bootstrap/stage1',), system_packages(root))


def system_packages(root):
    return {
        read_manifest(path).get('package', {}).get('name')
        for path in (root / 'lib').rglob('Cargo.toml')
        if 'target' not in path.relative_to(root / 'lib').parts
    }


def check_stdlib(root):
    """The common substrate itself must never pull policy from lib or bootstrap."""
    pending = [root / 'stdlib/rust']
    pending.extend(member_directories(root / 'stdlib/rust/Cargo.toml'))
    visited = set()
    forbidden = system_packages(root)
    while pending:
        directory = pending.pop()
        if directory in visited:
            continue
        visited.add(directory)
        for alias, spec in dependencies(read_manifest(directory / 'Cargo.toml')):
            if package_name(alias, spec) in forbidden:
                raise ValueError(f'{directory}: {alias} escapes the stdlib substrate')
            if not isinstance(spec, dict) or 'path' not in spec:
                continue
            target = (directory / spec['path']).resolve()
            if not any(target.is_relative_to(root / base)
                       for base in ('stdlib', 'sdk/abi', 'third_party')):
                raise ValueError(f'{directory}: {alias} escapes the stdlib substrate')
            pending.append(target)


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
    check_bootstrap(root)
    check_stdlib(root)
    index = subprocess.run(['git', 'ls-files', '--stage', '-z'], cwd=root,
                           check=True, capture_output=True).stdout
    check_index(index)
    print('workspace dependency declarations verified')


if __name__ == '__main__':
    main()
