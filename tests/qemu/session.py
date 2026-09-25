# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Owned serial subprocess transport; guest expectations belong to scenarios."""
import os
import re
import selectors
import subprocess
import time


class Session:
    def __init__(self, command, logfile, *, failures=(b'HypeR: fatal', b'kernel panic'),
                 cleanup_timeout=3, output_filter=None):
        self.command = command
        self.logfile = logfile
        self.failures = failures
        self.cleanup_timeout = cleanup_timeout
        self.output_filter = output_filter
        self.pending = bytearray()
        self.process = None
        self.selector = None
        self.log = None
        self.eof = False

    def __enter__(self):
        self.log = open(self.logfile, 'wb')
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, kind, error, traceback):
        self.close()

    def close(self):
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=self.cleanup_timeout)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=self.cleanup_timeout)
                # QEMU has no inherited child writers. Drain only currently
                # available bytes so an unexpected inherited pipe cannot hang.
                if self.selector is not None:
                    while self.selector.select(0):
                        if not self._read(0):
                            break
                self.process.stdin.close()
                self.process.stdout.close()
        finally:
            if self.selector is not None:
                self.selector.close()
            if self.log is not None:
                self.log.close()

    def _read(self, timeout):
        progress = False
        for key, _ in self.selector.select(timeout):
            data = os.read(key.fd, 65536)
            if not data:
                self.eof = True
                self.selector.unregister(key.fileobj)
                continue
            progress = True
            self.log.write(data)
            self.log.flush()
            self.pending.extend(data.replace(b'\r', b''))
        return progress

    def _failure(self):
        # Inspect custom failure markers before a scenario filters logs, then
        # check again after reconstructing text split by those records. Keep
        # filtering out of _read so cleanup always drains and closes pipes.
        if any(marker in self.pending for marker in self.failures):
            raise RuntimeError(f'kernel failure: {bytes(self.pending[-4096:])!r}')
        if self.output_filter is not None:
            self.output_filter(self.pending, b'')
        if any(marker in self.pending for marker in self.failures):
            raise RuntimeError(f'kernel failure: {bytes(self.pending[-4096:])!r}')

    def _exited(self):
        code = self.process.poll()
        if code is not None:
            while self._read(0):
                pass
            raise RuntimeError(f'QEMU exited with {code}: {bytes(self.pending[-8192:])!r}')
        if self.eof:
            raise RuntimeError('QEMU closed serial output before exiting')

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            self._read(min(0.02, remaining))
            self._failure()
            self._exited()

    def await_text(self, pattern, timeout=60, *, match_only=False):
        deadline = time.monotonic() + timeout
        while True:
            self._failure()
            match = re.search(pattern, self.pending)
            if match:
                result = match.group(0) if match_only else bytes(self.pending[:match.end()])
                del self.pending[:match.end()]
                return result
            self._exited()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'waiting for {pattern!r}: {bytes(self.pending[-4096:])!r}')
            self._read(min(0.2, remaining))

    def send(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()
