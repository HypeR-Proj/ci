#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Download this run's resolved appliance, without compiling I/O VM sources."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--platform', choices=['qemu', 'rpi5'], required=True)
    parser.add_argument('--materials', action='store_true')
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    command = [sys.executable, str(args.workspace / 'scripts/fetch-io-vm.py'),
               '--reference', snapshot['io_packages'][args.platform], '--platform', args.platform]
    if args.materials:
        command.append('--materials')
    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
