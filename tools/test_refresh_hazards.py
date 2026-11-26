from datetime import datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import socket
import unittest
from unittest import mock
import urllib.error
import urllib.request

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
        self.assertEqual('2026-11-24T21:21:23Z', refresh.page_timestamp(
            'contains all OSM data up to <time title="Tuesday, 24.11.2026, 21:21:23 UTC", '
            'datetime="2026-11-24T21:21:23+00:00">1 day ago</time>.'))
        for page in ('all OSM data up to <time datetime="2026-11-24T21:21:23+03:00">',
                     'all OSM data up to 2026-10-28T21:21:02Z. all OSM data up to 2026-10-29T21:21:02Z.'):
            with self.assertRaises(ValueError): refresh.page_timestamp(page)
        self.assertEqual('a'*32, refresh.checksum('a'*32 + '  iraq-latest.osm.pbf\n'))
        for value in ('a'*32 + ' other.osm.pbf', '<html>Error</html>'):
            with self.assertRaises(ValueError): refresh.checksum(value)
        with self.assertRaises(ValueError): refresh.page_timestamp('missing source date')

    def test_source_redirect_allows_only_one_dated_extract_within_the_same_origin(self):
        handler = refresh.NoRedirect()
        for origin in refresh.ORIGINS:
            request = urllib.request.Request(origin + 'iraq-latest.osm.pbf')
            target = origin + 'iraq-261028.osm.pbf'
            redirected = handler.redirect_request(request, None, 302, 'Found', {}, target)
            self.assertEqual(target, redirected.full_url)
            other = next(o for o in refresh.ORIGINS if o != origin)
            for url in ('http://download.geofabrik.de/asia/iraq/iraq-261028.osm.pbf',
                        'https://download.geofabrik.de/asia/iraq-261028.osm.pbf',
                        'https://example.com/iraq-261028.osm.pbf', other + 'iraq-261028.osm.pbf',
                        origin + 'iran-261028.osm.pbf', target + '?extra=1'):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    handler.redirect_request(request, None, 302, 'Found', {}, url)
            with self.assertRaises(ValueError):
                handler.redirect_request(redirected, None, 302, 'Found', {}, target)

    def test_unreachable_primary_falls_back_to_relay_without_backoff(self):
        seen, sleeps = [], []
        def primary_down(name, limit, output=None, *, origin, timeout):
            seen.append((origin, timeout))
            if origin == refresh.ORIGIN:
                raise urllib.error.URLError(OSError(101, 'Network is unreachable'))
            return b'ok'
        with mock.patch.object(refresh, 'fetch_once', primary_down):
            self.assertEqual(b'ok', refresh.fetch('index.html', 1024, sleep=sleeps.append))
        self.assertEqual([(refresh.ORIGIN, 8), (refresh.RELAY, 60)], seen)
        self.assertEqual([], sleeps)

    def test_transient_network_faults_retry_three_rounds_with_backoff(self):
        calls, sleeps = [], []
        def flaky(name, limit, output=None, **kwargs):
            calls.append(name)
            if len(calls) < 3:
                raise urllib.error.URLError(OSError(101, 'Network is unreachable'))
            return b'ok'
        with mock.patch.object(refresh, 'fetch_once', flaky):
            self.assertEqual(b'ok', refresh.fetch('index.html', 1024, sleep=sleeps.append))
        self.assertEqual([5], sleeps)
        calls.clear()
        sleeps.clear()
        def always(name, limit, output=None, **kwargs):
            calls.append(name)
            raise urllib.error.HTTPError(name, 503, 'busy', {}, None)
        with mock.patch.object(refresh, 'fetch_once', always), self.assertRaises(urllib.error.HTTPError):
            refresh.fetch('index.html', 1024, sleep=sleeps.append)
        self.assertEqual(6, len(calls))
        self.assertEqual([5, 10], sleeps)

    def test_guard_failures_and_client_errors_are_not_retried_or_relayed(self):
        for error in (ValueError('Unexpected source redirect'), urllib.error.HTTPError('u', 404, 'missing', {}, None)):
            calls = []
            def fail(name, limit, output=None, **kwargs):
                calls.append(kwargs['origin'])
                raise error
            with self.subTest(error=error), mock.patch.object(refresh, 'fetch_once', fail), self.assertRaises(type(error)):
                refresh.fetch('index.html', 1024, sleep=lambda s: self.fail('must not retry'))
            self.assertEqual([refresh.ORIGIN], calls)

    def test_retry_discards_partial_download_bytes(self):
        stream, calls = io.BytesIO(), []
        def partial(name, limit, output=None, **kwargs):
            calls.append(name)
            output.write(b'partial' if len(calls) == 1 else b'full')
            if len(calls) == 1:
                raise TimeoutError()
            return b''
        with mock.patch.object(refresh, 'fetch_once', partial):
            refresh.fetch('iraq-latest.osm.pbf', 1024, stream, sleep=lambda s: None)
        self.assertEqual(b'full', stream.getvalue())

    def test_ipv4_addresses_are_tried_first_and_resolver_restored(self):
        original = socket.getaddrinfo
        v6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 443, 0, 0))
        v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))
        with mock.patch.object(socket, 'getaddrinfo', lambda *a, **k: [v6, v4]):
            with refresh.ipv4_first():
                self.assertEqual([v4, v6], socket.getaddrinfo('download.geofabrik.de', 443))
        self.assertIs(original, socket.getaddrinfo)


if __name__ == '__main__':
    unittest.main()
