import struct
import tempfile
import unittest
from pathlib import Path

from retention.artifacts import DAY_NS, read_json
from retention.dataset import inspect_part, prepare, verify


def journal(path, times, sequences, sealed=True):
    header = bytearray(256)
    struct.pack_into('<4sIIIQ', header, 0, b'FMJ1', 3, 256, 0, 42)
    payload = bytearray()
    for at, sequence in zip(times, sequences):
        event = bytearray(128)
        struct.pack_into('<IBBBBIIQQqqQII', event, 0, 128, 25, 1, 0, 16, 0, 0, sequence, 0, at, at, 0, 0, 0)
        struct.pack_into('<q', event, 64, at)
        payload.extend(event)
    block = struct.pack('<4sIQQIII28s', b'FMJB', len(payload), sequences[0], sequences[-1], len(times), 0, 0, bytes(28))
    trailer = struct.pack('<4sIQQIII28s', b'FMJB', 0, 0, 0, 0, 0, 1, bytes(28)) if sealed else b''
    path.write_bytes(header + block + payload + trailer)


class DatasetContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reconstructs_engine_clock_and_requires_sealed_parts(self):
        path = self.root / 'fixture.fmj'
        journal(path, [100, 200], [1, 2])
        self.assertEqual(inspect_part(path, None, None), (2, 200, 100, 200, 42))
        journal(path, [100, 200], [1, 3])
        with self.assertRaisesRegex(ValueError, 'sequence gap'):
            inspect_part(path, None, None)
        journal(path, [100], [1], sealed=False)
        with self.assertRaisesRegex(ValueError, 'no trailer'):
            inspect_part(path, None, None)

    def test_immutable_snapshot_binds_hashes_and_rejects_extra_parts(self):
        source = self.root / 'fixture.fmj'
        start, end = DAY_NS, 8 * DAY_NS
        journal(source, [start - 60_000_000_000, end + 30_000_000_000], [1, 2])
        funding = self.root / 'funding.json'
        funding.write_text('{}')
        output = self.root / 'snapshot'
        manifest = prepare(source, output, start, end, funding, 'train')
        verify(manifest)
        self.assertEqual(read_json(output / 'dataset_manifest.json'), manifest)
        journal(output / 'fixture.1.fmj', [end + 40_000_000_000], [3])
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            verify(manifest)


if __name__ == '__main__':
    unittest.main()
