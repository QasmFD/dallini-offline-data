"""Refresh the public Iraq sign snapshot; never writes owner edits or routing data."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import urllib.request

from validate_hazards import parse, read, validate_revision

ORIGIN = 'https://download.geofabrik.de/asia/'
MAX_SOURCE = 200 * 1024 * 1024


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def page_timestamp(text):
    values = re.findall(r'all OSM data up to\s*(?:<[^>]+>)*\s*(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)', text)
    if len(values) != 1:
        raise ValueError('Official source page must expose one snapshot timestamp')
    datetime.strptime(values[0], '%Y-%m-%dT%H:%M:%SZ')
    return values[0]


def checksum(text):
    match = re.fullmatch(r'([a-f0-9]{32})\s+\*?iraq-latest\.osm\.pbf\s*', text)
    if not match:
        raise ValueError('Unexpected official checksum response')
    return match[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Unexpected source redirect')


def fetch(name, limit, output=None):
    if name not in ('iraq.html', 'iraq-latest.osm.pbf.md5', 'iraq-latest.osm.pbf'):
        raise ValueError('Unknown source resource')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(ORIGIN + name, headers={'User-Agent': 'Dallini-public-map-refresh/1.0', 'Accept-Encoding': 'identity'})
    start, chunks, size = time.monotonic(), [], 0
    with opener.open(request, timeout=60) as response:
        if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
            raise ValueError('Unexpected source response')
        declared = response.headers.get('Content-Length')
        if declared is not None and int(declared) > limit:
            raise ValueError('Source exceeds byte budget')
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > limit or time.monotonic() - start > 300:
                raise ValueError('Source exceeds bounded download budget')
            if output is not None:
                output.write(block)
            else:
                chunks.append(block)
        if declared is not None and size != int(declared):
            raise ValueError('Incomplete source download')
    return b''.join(chunks)


def prepare(raw, manifest, previous):
    if (manifest['bytes'] != len(raw) or manifest['sha256'] != hashlib.sha256(raw).hexdigest()
            or manifest['published_md5_verified'] is not True or manifest['latest_snapshot_verified'] is not True
            or manifest['invalid_geometry'] != 0):
        raise ValueError('Extract integrity or geometry check failed')
    candidate = json.loads(raw)
    if not candidate['features'] or candidate['osm_timestamp'] != manifest['osm_timestamp']:
        raise ValueError('Empty automatic extract or conflicting timestamp')
    candidate.update(schema_version=1, revision=previous['revision'], source_sha256=manifest['source_sha256'])
    parse(encode(candidate))
    if candidate == previous:
        return None
    candidate['revision'] += 1
    validate_revision(candidate, previous)
    return parse(encode(candidate))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source', type=Path, help='Existing source bytes for reproducibility verification')
    parser.add_argument('--verify-current', action='store_true', help='Reproduce extraction even when the official checksum is unchanged')
    args = parser.parse_args()
    folder = args.root / 'hazards'
    current_path = folder / 'road-hazards.geojson'
    previous_bytes = current_path.read_bytes()
    previous = parse(previous_bytes)
    page = fetch('iraq.html', 1024 * 1024).decode('utf-8')
    timestamp = page_timestamp(page)
    expected = checksum(fetch('iraq-latest.osm.pbf.md5', 1024).decode('ascii'))
    if timestamp < previous['osm_timestamp']:
        raise ValueError('Official source date regressed; keeping current data')
    provenance_path = folder / 'provenance.json'
    prior = json.loads(provenance_path.read_text(encoding='utf-8'))
    if (not args.source and not args.verify_current and timestamp == previous['osm_timestamp'] and expected == prior.get('source_md5')
            and prior.get('display_snapshot', {}).get('sha256') == hashlib.sha256(previous_bytes).hexdigest()):
        print(json.dumps({'changed': False, 'revision': previous['revision'], 'reason': 'official snapshot unchanged'}))
        return
    # Import native dependencies only when a fresh extraction is needed.
    from build_native_road_hazards import build
    with tempfile.TemporaryDirectory(prefix='dallini-signs-') as temporary:
        stage = Path(temporary)
        source = args.source or stage / 'iraq-latest.osm.pbf'
        if not args.source:
            with source.open('xb') as stream:
                fetch('iraq-latest.osm.pbf', MAX_SOURCE, stream)
        if checksum(fetch('iraq-latest.osm.pbf.md5', 1024).decode('ascii')) != expected:
            raise ValueError('Official publication changed during download; keep current data')
        extracted = stage / 'extracted.geojson'
        manifest = build(source, extracted, expected, timestamp)
        candidate = prepare(extracted.read_bytes(), manifest, previous)
        if candidate is None:
            print(json.dumps({'changed': False, 'revision': previous['revision'], 'reason': 'verified identical extraction'}))
            return
        payload = encode(candidate)
        receipt = dict(revision=candidate['revision'], bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                       features=len(candidate['features']), osm_timestamp=timestamp, source_sha256=candidate['source_sha256'],
                       features_by_kind=manifest['features_by_kind'])
        provenance = manifest | {'display_snapshot': receipt, 'refreshed_at': datetime.now(timezone.utc).isoformat()}
        # Neither file is externally visible until the workflow commits both together.
        if current_path.read_bytes() != previous_bytes:
            raise ValueError('Snapshot changed concurrently')
        current_path.write_bytes(payload)
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'changed': True, **receipt}))


if __name__ == '__main__':
    main()
