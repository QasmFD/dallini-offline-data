"""Build offline Iraq speed-bump and police signs from a verified OSM extract.

Source semantics: https://wiki.openstreetmap.org/wiki/Key:traffic_calming
https://wiki.openstreetmap.org/wiki/Tag:police%3Dcheckpoint
https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dpolice

This records mapped objects, not live officers or a claim of complete surveys.
No inferred coordinates, user reports, or routing restrictions are generated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import osmium
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'static/map-assets/road-hazards.geojson'
VERTICAL_CALMING = {'bump', 'hump', 'table', 'cushion', 'mini_bumps', 'rumble_strip'}
INACTIVE = {'yes', 'true', '1'}
PROPERTY_KEYS = ('name', 'name:ar', 'name:en', 'name:ku', 'name:ckb', 'ref', 'operator',
                 'traffic_calming', 'amenity', 'police', 'highway', 'direction', 'maxspeed',
                 'opening_hours', 'check_date', 'survey:date', 'start_date', 'end_date')


def kinds(tags):
    if any(tags.get(key) in INACTIVE for key in ('disused', 'abandoned', 'demolished', 'removed', 'construction', 'proposed')):
        return []
    result = []
    calming = {part.strip() for part in tags.get('traffic_calming', '').split(';')}
    if calming & VERTICAL_CALMING:
        result.append('speed_bump')
    # OSM explicitly distinguishes police, military, and border checkpoints.
    # Neither a generic checkpoint nor a station implies an officer on the road.
    if tags.get('police') == 'checkpoint':
        result.append('police_checkpoint')
    elif tags.get('amenity') == 'police':
        result.append('police_station')
    if tags.get('highway') == 'speed_camera':
        result.append('speed_camera')
    return result


class Hazards(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.records = {}
        self.counts = Counter()
        self.factory = osmium.geom.GeoJSONFactory()

    @property
    def features(self):
        return [self.records[key] for key in sorted(self.records)]

    def save(self, identifier, tags, point, origin):
        if point.is_empty or not (-180 <= point.x <= 180 and -85.05112878 <= point.y <= 85.05112878):
            self.counts['invalid_geometry'] += 1
            return
        for kind in kinds(tags):
            feature_id = f'{identifier}:{kind}'
            _, osm_type, osm_id = identifier.split(':')
            properties = {key: str(tags[key]) for key in PROPERTY_KEYS if tags.get(key)}
            properties.update(kind=kind, source_id=identifier, source='OpenStreetMap',
                              osm_type=osm_type, osm_id=int(osm_id),
                              license='ODbL-1.0', geometry_origin=origin)
            self.records[feature_id] = {'type': 'Feature', 'id': feature_id,
                                       'geometry': {'type': 'Point', 'coordinates': [point.x, point.y]},
                                       'properties': properties}

    def node(self, node):
        if kinds(dict(node.tags)) and node.location.valid():
            self.save(f'osm:node:{node.id}', dict(node.tags),
                      shape({'type': 'Point', 'coordinates': [node.lon, node.lat]}), 'mapped_node')

    def way(self, way):
        tags = dict(way.tags)
        if not kinds(tags):
            return
        # Closed police footprints are supplied by Osmium's area assembler.
        # Linear calming devices retain their recorded way with a midpoint icon.
        if way.is_closed() and tags.get('area') != 'no' and tags.get('amenity') == 'police':
            return
        try:
            line = shape(json.loads(self.factory.create_linestring(way)))
            self.save(f'osm:way:{way.id}', tags, line.interpolate(0.5, normalized=True), 'mapped_way_midpoint')
        except (RuntimeError, ValueError, osmium.InvalidLocationError):
            self.counts['invalid_geometry'] += 1

    def area(self, area):
        tags = dict(area.tags)
        if not kinds(tags):
            return
        try:
            geometry = shape(json.loads(self.factory.create_multipolygon(area)))
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            identifier = f'osm:{"way" if area.from_way() else "relation"}:{area.orig_id()}'
            self.save(identifier, tags, geometry.representative_point(), 'mapped_area_representative_point')
        except (RuntimeError, ValueError, osmium.InvalidLocationError):
            self.counts['invalid_geometry'] += 1


def extract(source):
    handler = Hazards()
    # Osmium populates node locations before the native key filter. Areas are
    # assembled for closed ways and multipolygon relations in the same pass.
    handler.apply_file(str(source), locations=True, idx='flex_mem',
                       filters=[osmium.filter.KeyFilter('traffic_calming', 'amenity', 'police', 'highway')])
    handler.counts.update(feature['properties']['kind'] for feature in handler.features)
    return handler


def file_digest(path, algorithm):
    checksum = hashlib.new(algorithm)
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def build(source, output=DEFAULT_OUTPUT, expected_md5=None, latest_timestamp=None):
    source, output = Path(source), Path(output)
    actual_md5 = file_digest(source, 'md5')
    if expected_md5 is not None and actual_md5.lower() != expected_md5.lower():
        raise ValueError('Source MD5 does not match the published Geofabrik checksum')
    with osmium.io.Reader(str(source)) as reader:
        timestamp = reader.header().get('osmosis_replication_timestamp') or reader.header().get('timestamp')
    if not timestamp:
        raise ValueError('Source is missing its OSM snapshot timestamp')
    if latest_timestamp is not None and timestamp != latest_timestamp:
        raise ValueError('Source does not match the latest verified Geofabrik timestamp')
    handler = extract(source)
    collection = {'type': 'FeatureCollection', 'name': 'Iraq source-recorded road signs',
                  'attribution': '© OpenStreetMap contributors, ODbL 1.0',
                  'osm_timestamp': timestamp, 'features': handler.features}
    payload = json.dumps(collection, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    manifest = {'asset': output.name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
                'source': source.name, 'source_url': 'https://download.geofabrik.de/asia/iraq.html',
                'osm_timestamp': timestamp, 'source_sha256': file_digest(source, 'sha256'),
                'source_md5': actual_md5, 'published_md5_verified': expected_md5 is not None,
                'latest_snapshot_verified': latest_timestamp is not None,
                'verified_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'license': 'ODbL-1.0', 'features_by_kind': dict(Counter(f['properties']['kind'] for f in handler.features)),
                'invalid_geometry': handler.counts['invalid_geometry'],
                'coverage': 'All relevant source-recorded nodes, ways, and areas in the Iraq extract. OSM survey coverage is incomplete; these are not live officer locations.',
                'geometry_policy': 'Mapped node coordinates; midpoint for linear ways; interior representative point for mapped areas. No fabricated objects or coordinates.',
                'schema': 1}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.geojson.tmp')
    temporary.write_bytes(payload)
    temporary.replace(output)
    output.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--expected-md5', required=True, help='Published checksum fetched from the official Iraq download server')
    parser.add_argument('--latest-timestamp', required=True, help='Verified latest replication timestamp, e.g. 2026-10-15T20:21:00Z')
    options = parser.parse_args()
    build(options.source, options.output, options.expected_md5, options.latest_timestamp)
