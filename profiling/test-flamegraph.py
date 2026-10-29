#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Reject corrupt captures and preserve frame/world/loss evidence in exports."""
import json
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/profiling'))
from flamegraph import captures, svg


class CaptureTests(unittest.TestCase):
    def records(self):
        return [dict(format='hyper-pmu-v1', address='kernel-image-offset', event='cpu-cycles',
                     cpus=2, period=1000000, kernel='abc 2026-01-01T00:00:00Z'),
                dict(cpu=0, sequence=5, world=0, frames=['10','20'], incomplete=False, dropped=2),
                dict(cpu=1, sequence=0, world=2, frames=[], incomplete=False, dropped=0),
                dict(end=True, samples=2, overwritten=5, irq_dropped=3)]

    def parse(self, records):
        return captures(map(json.dumps, records), lambda address: f'frame<{address}>')

    def test_call_order_loss_and_safe_svg(self):
        stacks, metadata = self.parse(self.records())
        self.assertEqual(stacks['cpu0;kernel;frame<32>;frame<16>'], 1)
        self.assertEqual(stacks['cpu1;[guest: no unwind]'], 1)
        self.assertEqual(metadata['irq_dropped'], 3)  # includes drops after last sample
        self.assertEqual(metadata['overwritten'], 5)
        image = svg(stacks, metadata)
        ElementTree.fromstring(image)
        self.assertIn('frame&lt;16&gt;', image)

    def test_truncation_duplicate_and_forged_summary(self):
        records = self.records()
        variants = [records[:-1], records + [records[1]],
                    records[:2] + [records[1]] + records[2:],
                    records[:-1] + [dict(end=True,samples=3,overwritten=5,irq_dropped=3)],
                    records[:-1] + [dict(end=True,samples=2,overwritten=5,irq_dropped=1)]]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(value)

    def test_invalid_address_and_guest_frames(self):
        for world, frames in [(2, ['10']), (0, ['-1']), (0, ['10000000000000000'])]:
            records = self.records()
            records[1].update(world=world, frames=frames)
            with self.subTest(world=world, frames=frames), self.assertRaises(ValueError):
                self.parse(records)

    def test_counts_are_unsigned_integers(self):
        for bad in (True, 1.5, -1, 1 << 64):
            records = self.records()
            records[1]['sequence'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.parse(records)

    def test_empty_capture_cannot_claim_a_flamegraph(self):
        records = [self.records()[0], dict(end=True,samples=0,overwritten=0,irq_dropped=0)]
        stacks, metadata = self.parse(records)
        with self.assertRaises(ValueError):
            svg(stacks, metadata)


if __name__ == '__main__':
    unittest.main()
