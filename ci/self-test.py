#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Test the CI coordinator and repository tooling without booting a system."""
from pathlib import Path
import subprocess
import sys

component = Path(__file__).resolve().parents[1]
root = component.parent

def run(*command):
    subprocess.run(command, cwd=root, check=True)

run(sys.executable, '-m', 'unittest', 'discover', '-s', str(component / 'tools/tests'))
for pattern in ('ci/test-*.py',):
    for path in sorted(component.glob(pattern)):
        run(sys.executable, '-B', str(path))
run('sh', str(component / 'ci/test-license-headers.sh'))
run(sys.executable, '-m', 'compileall', '-q', str(component / 'tools'))
