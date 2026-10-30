"""Validate the bounded public display-only owner map format (standard library)."""
import argparse
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

MAX_BYTES = 1024 * 1024
MAX_FEATURES = 2000
MAX_POINTS_PER_FEATURE = 500
MAX_TOTAL_POINTS = 100000
MAX_REVISION = 9007199254740991
ATTRIBUTION = 'Dallini owner map edits'
BUMPS_ATTRIBUTION = 'Dallini owner bumps'
BUMPS_SUBTYPES = ('bump', 'hump', 'table', 'cushion', 'choker', 'chicane', 'rumble_strip', 'dip', 'yes')
SOURCE_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', re.ASCII)
OWNER_ID = re.compile(r'owner:[a-z0-9][a-z0-9_-]{0,63}', re.ASCII)


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('Unexpected or missing object fields')


def coordinate(point):
    if not isinstance(point, list) or len(point) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) for n in point):
        raise ValueError('Coordinate must be a finite numeric longitude/latitude pair')
    longitude, latitude = point
    if not 36.5 <= longitude <= 50.5 or not 27 <= latitude <= 39:
        raise ValueError('Coordinate is outside the supported Iraq envelope')


def validate(value, feed='roads'):
    """Validate an already decoded object; byte limits are enforced by parse()."""
    if feed not in ('roads', 'bumps'):
        raise ValueError('Unknown display feed')
    fields(value, ('type', 'schema_version', 'revision', 'attribution', 'features'))
    if value['type'] != 'FeatureCollection' or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Expected owner FeatureCollection schema 1')
    if type(value['revision']) is not int or not 1 <= value['revision'] <= MAX_REVISION:
        raise ValueError('Revision must be a positive safe integer')
    if value['attribution'] != (BUMPS_ATTRIBUTION if feed == 'bumps' else ATTRIBUTION):
        raise ValueError('Owner attribution must remain unchanged')
    features = value['features']
    if not isinstance(features, list) or len(features) > MAX_FEATURES:
        raise ValueError('Expected at most 2000 features')
    seen = set()
    total = 0
    for feature in features:
        fields(feature, ('type', 'id', 'properties', 'geometry'))
        identity = feature['id']
        if feature['type'] != 'Feature' or not isinstance(identity, str) or not OWNER_ID.fullmatch(identity) or identity in seen:
            raise ValueError('Each feature needs a unique bounded owner ID')
        seen.add(identity)
        properties = feature['properties']
        if feed == 'bumps':
            fields(properties, ('kind', 'subtype', 'source', 'source_date'), ('name',))
            if properties['kind'] != 'bump' or properties['subtype'] not in BUMPS_SUBTYPES or properties['source'] != 'osm':
                raise ValueError('Expected an OSM-declared traffic calming point with a supported subtype')
            source_date = properties['source_date']
            if not isinstance(source_date, str) or not SOURCE_DATE.fullmatch(source_date):
                raise ValueError('Source date must be a real YYYY-MM-DD calendar date')
            try:
                date.fromisoformat(source_date)
            except ValueError as exc:
                raise ValueError('Source date must be a real YYYY-MM-DD calendar date') from exc
        else:
            fields(properties, ('kind',), ('name',))
            if properties['kind'] != 'road':
                raise ValueError('Only display road features are supported')
        if 'name' in properties:
            name = properties['name']
            if (not isinstance(name, str) or len(name) > 120 or name != name.strip()
                    or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') or c in '<>' for c in name)):
                raise ValueError('Name must be trimmed plain text of at most 120 code points')
        geometry = feature['geometry']
        fields(geometry, ('type', 'coordinates'))
        points = geometry['coordinates']
        if feed == 'bumps':
            if geometry['type'] != 'Point':
                raise ValueError('Traffic calming geometry must be a single Point')
            coordinate(points)
            continue
        if geometry['type'] != 'LineString' or not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS_PER_FEATURE:
            raise ValueError('Road geometry must contain 2 to 500 LineString points')
        total += len(points)
        if total > MAX_TOTAL_POINTS:
            raise ValueError('Collection exceeds 100000 points')
        distinct = set()
        for point in points:
            coordinate(point)
            distinct.add(tuple(point))
        if len(distinct) < 2:
            raise ValueError('Road needs at least two distinct positions')
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def invalid_number(_):
    raise ValueError('Nonfinite JSON numbers are not supported')


def parse(raw, feed='roads'):
    """Reject malformed UTF-8/JSON, duplicate fields and oversized snapshots."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError('Snapshot must be at most 1 MiB of UTF-8 JSON')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=invalid_number)
        return validate(value, feed=feed)
    except (UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError('Invalid encoded snapshot') from exc
    except json.JSONDecodeError as exc:
        raise ValueError('Invalid JSON snapshot') from exc


def read_snapshot(path, feed='roads'):
    with Path(path).open('rb') as stream:
        return parse(stream.read(MAX_BYTES + 1), feed=feed)


def validate_revision(current, previous, feed='roads'):
    """An unchanged snapshot may keep its revision; any change must increase it."""
    validate(current, feed=feed)
    validate(previous, feed=feed)
    if current['revision'] < previous['revision']:
        raise ValueError('Snapshot revision cannot decrease')
    if current['revision'] == previous['revision'] and current != previous:
        raise ValueError('Changed snapshot must increase its revision')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Owner edits/map.geojson or edits/bumps.geojson file')
    parser.add_argument('--feed', choices=('roads', 'bumps'), default='roads', help='Explicit independent display feed; defaults to roads')
    parser.add_argument('--previous', type=Path, help='Previous accepted snapshot for revision validation')
    parser.add_argument('--output', type=Path, help='Optional new canonical output file; never overwritten')
    args = parser.parse_args()
    try:
        value = read_snapshot(args.input, feed=args.feed)
        if args.previous is not None:
            validate_revision(value, read_snapshot(args.previous, feed=args.feed), feed=args.feed)
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        if len(payload) > MAX_BYTES:
            raise ValueError('Canonical snapshot exceeds 1 MiB')
        if args.output is not None:
            with args.output.open('xb') as stream:
                stream.write(payload)
        print(json.dumps({'valid': True, 'feed': args.feed, 'schema_version': 1, 'revision': value['revision'],
                          'features': len(value['features']), 'canonical_bytes': len(payload),
                          'canonical_sha256': hashlib.sha256(payload).hexdigest()}, sort_keys=True))
        return 0
    except (ValueError, OSError) as exc:
        # Never print map coordinates, names, arbitrary keys or supplied field values.
        detail = str(exc) if isinstance(exc, ValueError) else 'Unable to read input or create new output'
        print('Map validation failed: ' + detail, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
