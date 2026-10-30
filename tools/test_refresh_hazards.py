from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import unittest

import refresh_hazards as refresh


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.previous = json.loads((Path(__file__).resolve().parents[1] / 'hazards/road-hazards.geojson').read_text(encoding='utf-8'))
        self.collection = {k: v for k, v in self.previous.items() if k not in ('schema_version', 'revision', 'source_sha256')}
        raw = refresh.encode(self.collection)
        self.manifest = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), source_sha256=self.previous['source_sha256'],
                             osm_timestamp=self.previous['osm_timestamp'], published_md5_verified=True,
                             latest_snapshot_verified=True, invalid_geometry=0)

    def test_same_source_is_idempotent_without_new_revision(self):
        self.assertIsNone(refresh.prepare(refresh.encode(self.collection), self.manifest, self.previous))

    def test_new_source_advances_revision_and_keeps_real_identities(self):
        stamp = datetime.strptime(self.previous['osm_timestamp'], '%Y-%m-%dT%H:%M:%SZ')
        self.collection['osm_timestamp'] = (stamp + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.manifest['osm_timestamp'] = self.collection['osm_timestamp']
        self.manifest['source_sha256'] = 'a' * 64
        raw = refresh.encode(self.collection)
        self.manifest.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        snapshot = refresh.prepare(raw, self.manifest, self.previous)
        self.assertEqual(self.previous['revision'] + 1, snapshot['revision'])
        self.assertEqual(self.previous['features'], snapshot['features'])

    def test_corrupt_or_unverified_extract_is_rejected(self):
        for key, value in [('bytes', 1), ('sha256', '0'*64), ('invalid_geometry', 1),
                           ('published_md5_verified', False), ('latest_snapshot_verified', False),
                           ('osm_timestamp', '2000-01-01T00:00:00Z')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                refresh.prepare(refresh.encode(self.collection), self.manifest | {key: value}, self.previous)

    def test_older_source_and_empty_automatic_extract_preserve_previous(self):
        for change in ({'osm_timestamp': '2000-01-01T00:00:00Z'}, {'features': []}):
            collection = self.collection | change
            raw = refresh.encode(collection)
            manifest = self.manifest | dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), osm_timestamp=collection['osm_timestamp'])
            with self.subTest(change=change.keys()), self.assertRaises(ValueError):
                refresh.prepare(raw, manifest, self.previous)

    def test_official_metadata_requires_one_timestamp_and_exact_checksum_name(self):
        self.assertEqual('2026-10-28T21:21:02Z', refresh.page_timestamp('all OSM data up to 2026-10-28T21:21:02Z.'))
        self.assertEqual('a'*32, refresh.checksum('a'*32 + '  iraq-latest.osm.pbf\n'))
        for value in ('a'*32 + ' other.osm.pbf', '<html>Error</html>'):
            with self.assertRaises(ValueError): refresh.checksum(value)
        with self.assertRaises(ValueError): refresh.page_timestamp('missing source date')


if __name__ == '__main__':
    unittest.main()
