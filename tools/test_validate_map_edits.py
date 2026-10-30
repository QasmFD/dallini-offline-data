"""Portable public-data contract tests; no network or application dependencies."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('validate_map_edits', Path(__file__).with_name('validate_map_edits.py'))
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def collection():
    # Synthetic geometry is test data only, never the public seed.
    return {'type': 'FeatureCollection', 'schema_version': 1, 'revision': 1,
            'attribution': 'Dallini owner map edits', 'features': [
                {'type': 'Feature', 'id': 'owner:test-road', 'properties': {'kind': 'road', 'name': 'شارع تجريبي'},
                 'geometry': {'type': 'LineString', 'coordinates': [[44, 33], [44.001, 33.001]]}}]}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


class TestOwnerMapEdits(unittest.TestCase):
    def test_valid_arabic_road_retains_geometry_and_name(self):
        value = collection()
        self.assertEqual(value, validator.parse(encoded(value)))

    def test_empty_snapshot_can_retract_all_roads(self):
        value = collection()
        value['features'] = []
        self.assertEqual([], validator.parse(encoded(value))['features'])

    def test_unknown_fields_reject_private_and_routing_metadata(self):
        for path, key in [((), 'device_id'), (('features', 0), 'raw_trace'),
                          (('features', 0, 'properties'), 'access'),
                          (('features', 0, 'geometry'), 'crs')]:
            with self.subTest(path=path):
                value = collection()
                target = value
                for item in path:
                    target = target[item]
                target[key] = 'private-or-routing-data'
                with self.assertRaises(ValueError):
                    validator.parse(encoded(value))

    def test_missing_keys_and_wrong_object_types_fail(self):
        for key in collection():
            value = collection()
            del value[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validator.parse(encoded(value))
        for value in [None, [], {'type': 'FeatureCollection'}, {'features': 'no'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validator.parse(encoded(value))

    def test_fixed_schema_revision_attribution(self):
        for key, bad in [('schema_version', True), ('schema_version', 2), ('schema_version', 1.0),
                         ('revision', True), ('revision', 0), ('revision', -1), ('revision', 1.1),
                         ('revision', 2**53), ('attribution', 'OpenStreetMap verified roads'),
                         ('type', 'GeometryCollection')]:
            with self.subTest(key=key, bad=bad):
                value = collection()
                value[key] = bad
                with self.assertRaises(ValueError):
                    validator.parse(encoded(value))

    def test_duplicate_json_keys_fail_including_nested_keys(self):
        for raw in [b'{"type":"FeatureCollection","type":"FeatureCollection"}',
                    encoded(collection()).replace(b'"kind":"road"', b'"kind":"road","kind":"road"')]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validator.parse(raw)

    def test_malformed_non_utf8_and_nonfinite_json_fail(self):
        for raw in [b'\xff', b'{}{}', b'{', b'{"revision": NaN}', b'{"revision":Infinity}',
                    b'[' * 2000 + b']' * 2000]:
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                validator.parse(raw)

    def test_payload_byte_limit(self):
        value = encoded(collection())
        self.assertEqual(1, validator.parse(value + b' ' * (1048576 - len(value)))['revision'])
        with self.assertRaises(ValueError):
            validator.parse(value + b' ' * (1048577 - len(value)))

    def test_ids_are_bounded_unique_owner_ids(self):
        for bad in ['', 'osm:way:123', 'owner:../file', 'owner:A', 'owner:_a', 'owner:' + 'a' * 65, 1]:
            value = collection()
            value['features'][0]['id'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validator.parse(encoded(value))
        value = collection()
        value['features'].append(copy.deepcopy(value['features'][0]))
        with self.assertRaises(ValueError):
            validator.parse(encoded(value))

    def test_kind_and_geometry_cannot_grant_routing_authority(self):
        for key, bad in [('kind', 'route'), ('kind', None)]:
            value = collection()
            value['features'][0]['properties'][key] = bad
            with self.assertRaises(ValueError):
                validator.parse(encoded(value))
        for bad in ['Point', 'MultiLineString', 'Polygon', None]:
            value = collection()
            value['features'][0]['geometry']['type'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validator.parse(encoded(value))

    def test_optional_name_is_trimmed_bounded_plain_unicode(self):
        for bad in [' name', 'name ', 'a' * 121, '<road>', 'a\nroad', 'a\x7froad', 'a\x85road',
                    'a\u202eroad', 'a\u200broad', 123, None]:
            value = collection()
            value['features'][0]['properties']['name'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validator.parse(encoded(value))
        value = collection()
        value['features'][0]['properties']['name'] = '🚗' * 120
        self.assertEqual(value, validator.parse(encoded(value)))
        del value['features'][0]['properties']['name']
        self.assertEqual(value, validator.parse(encoded(value)))

    def test_coordinates_must_be_finite_pairs_inside_envelope(self):
        for bad in [[36.49, 33], [50.51, 33], [44, 26.99], [44, 39.01],
                    [44, 33, 0], ['44', 33], [True, 33], [44], None, [float('nan'), 33]]:
            value = collection()
            value['features'][0]['geometry']['coordinates'][0] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validator.parse(encoded(value))
        value = collection()
        value['features'][0]['geometry']['coordinates'] = [[36.5, 27], [50.5, 39]]
        self.assertEqual(value, validator.parse(encoded(value)))

    def test_each_road_has_two_distinct_points_and_at_most_500(self):
        for coordinates in [[], [[44, 33]], [[44, 33], [44.0, 33.0]], [[44, 33], [44.1, 33.1]] * 250 + [[44, 33]]]:
            value = collection()
            value['features'][0]['geometry']['coordinates'] = coordinates
            with self.subTest(length=len(coordinates)), self.assertRaises(ValueError):
                validator.parse(encoded(value))

    def test_maximum_feature_count(self):
        value = collection()
        feature = value['features'][0]
        value['features'] = [dict(copy.deepcopy(feature), id=f'owner:r{i}') for i in range(2000)]
        self.assertEqual(2000, len(validator.parse(encoded(value))['features']))
        value['features'].append(dict(copy.deepcopy(feature), id='owner:extra'))
        with self.assertRaises(ValueError):
            validator.parse(encoded(value))

    def test_total_point_count_is_independently_bounded(self):
        value = collection()
        feature = value['features'][0]
        feature['geometry']['coordinates'] = [[44, 33], [45, 34]] * 250
        value['features'] = [dict(copy.deepcopy(feature), id=f'owner:r{i}') for i in range(200)]
        self.assertEqual(100000, sum(len(f['geometry']['coordinates']) for f in validator.validate(value)['features']))
        value['features'].append(dict(copy.deepcopy(feature), id='owner:extra'))
        with self.assertRaises(ValueError):
            validator.validate(value)

    def test_revision_prevents_rollback_and_reuse_with_changed_content(self):
        previous = collection()
        previous['revision'] = 2
        current = collection()
        with self.assertRaises(ValueError):
            validator.validate_revision(current, previous)
        current['revision'] = 2
        current['features'] = []
        with self.assertRaises(ValueError):
            validator.validate_revision(current, previous)
        current['revision'] = 3
        validator.validate_revision(current, previous)

    def test_same_revision_ignores_json_format_and_key_order(self):
        previous = collection()
        current = dict(reversed(list(previous.items())))
        current['features'][0]['geometry']['coordinates'][0] = [44.0, 33.0]
        validator.validate_revision(current, previous)

    def test_cli_does_not_write_invalid_output_or_print_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'input.json'
            output = Path(folder) / 'output.json'
            value = collection()
            value['features'][0]['properties']['device_id'] = 'DO_NOT_LOG_PRIVATE_VALUE'
            source.write_bytes(encoded(value))
            result = subprocess.run([sys.executable, str(Path(validator.__file__)), str(source), '--output', str(output)], capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output.exists())
            self.assertNotIn('DO_NOT_LOG_PRIVATE_VALUE', result.stdout + result.stderr)

    def test_cli_writes_valid_snapshot_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'input.json'
            output = Path(folder) / 'output.json'
            source.write_bytes(encoded(collection()))
            command = [sys.executable, str(Path(validator.__file__)), str(source), '--output', str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(collection(), json.loads(output.read_text(encoding='utf-8')))
            original = output.read_bytes()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(original, output.read_bytes())


def bumps_collection():
    return json.loads((Path(__file__).parent / 'fixtures/baghdad-bumps-schema1.geojson').read_text(encoding='utf-8'))


class TestOwnerBumps(unittest.TestCase):
    def test_actual_published_nine_points_preserve_author_metadata(self):
        value = bumps_collection()
        self.assertEqual(9, len(value['features']))
        self.assertEqual(value, validator.parse(encoded(value), feed='bumps'))
        self.assertEqual({'bump', 'hump', 'choker', 'yes'}, {f['properties']['subtype'] for f in value['features']})

    def test_separate_feeds_cannot_accept_each_others_snapshot(self):
        with self.assertRaises(ValueError):
            validator.parse(encoded(bumps_collection()))
        with self.assertRaises(ValueError):
            validator.parse(encoded(collection()), feed='bumps')
        with self.assertRaises(ValueError):
            validator.parse(encoded(collection()), feed='unknown')

    def test_only_documented_subtypes_are_accepted(self):
        value = bumps_collection()
        for subtype in ['bump', 'hump', 'table', 'cushion', 'choker', 'chicane', 'rumble_strip', 'dip', 'yes']:
            value['features'][0]['properties']['subtype'] = subtype
            with self.subTest(subtype=subtype):
                self.assertEqual(value, validator.parse(encoded(value), feed='bumps'))
        for subtype in ['speed_camera', 'island', '', None, True, 1, ['bump'], 'BUMP']:
            value['features'][0]['properties']['subtype'] = subtype
            with self.subTest(subtype=subtype), self.assertRaises(ValueError):
                validator.parse(encoded(value), feed='bumps')

    def test_source_and_date_are_strict_declarations(self):
        value = bumps_collection()
        for key, invalid in [('kind', 'road'), ('source', 'OSM'), ('source', 'verified'), ('source', None),
                             ('source_date', '2026-02-29'), ('source_date', '1900-02-29'),
                             ('source_date', '2026-04-31'), ('source_date', '0000-01-01'),
                             ('source_date', '2026-1-01'), ('source_date', '2026-01-01T00:00:00Z'),
                             ('source_date', '٢٠٢٦-١٠-٢٨'), ('source_date', None)]:
            current = copy.deepcopy(value)
            current['features'][0]['properties'][key] = invalid
            with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                validator.parse(encoded(current), feed='bumps')
        for date in ['0001-01-01', '2000-02-29', '2024-02-29', '9999-12-31']:
            value['features'][0]['properties']['source_date'] = date
            with self.subTest(date=date):
                self.assertEqual(value, validator.parse(encoded(value), feed='bumps'))

    def test_metadata_is_required_and_private_fields_fail(self):
        for key in ['kind', 'subtype', 'source', 'source_date']:
            value = bumps_collection()
            del value['features'][0]['properties'][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validator.parse(encoded(value), feed='bumps')
        for key in ['device_id', 'raw_trace', 'verified', 'source_way_id', 'access']:
            value = bumps_collection()
            value['features'][0]['properties'][key] = 'not-public'
            with self.subTest(key=key), self.assertRaises(ValueError):
                validator.parse(encoded(value), feed='bumps')

    def test_only_finite_single_point_inside_iraq_envelope(self):
        for point in [[36.49, 33], [50.51, 33], [44, 26.99], [44, 39.01], [True, 33],
                      ['44', 33], [44, 33, 1], [[44, 33]], [float('inf'), 33], None]:
            value = bumps_collection()
            value['features'][0]['geometry']['coordinates'] = point
            with self.subTest(point=point), self.assertRaises(ValueError):
                validator.parse(encoded(value), feed='bumps')
        value = bumps_collection()
        value['features'][0]['geometry'] = collection()['features'][0]['geometry']
        with self.assertRaises(ValueError):
            validator.parse(encoded(value), feed='bumps')
        value['features'][0]['geometry'] = {'type': 'Point', 'coordinates': [36.5, 27]}
        self.assertEqual(value, validator.parse(encoded(value), feed='bumps'))

    def test_shared_id_name_duplicate_byte_and_feature_limits(self):
        value = bumps_collection()
        value['features'][0]['properties']['name'] = 'مطب'
        self.assertEqual(value, validator.parse(encoded(value), feed='bumps'))
        for name in [' x', '<x>', 'x\u202e', 'x' * 121]:
            value['features'][0]['properties']['name'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                validator.parse(encoded(value), feed='bumps')
        value = bumps_collection()
        value['features'].append(copy.deepcopy(value['features'][0]))
        with self.assertRaises(ValueError):
            validator.parse(encoded(value), feed='bumps')
        value['features'] = [dict(copy.deepcopy(value['features'][0]), id=f'owner:p{i}') for i in range(2000)]
        self.assertEqual(2000, len(validator.parse(encoded(value), feed='bumps')['features']))
        value['features'].append(dict(copy.deepcopy(value['features'][0]), id='owner:extra'))
        with self.assertRaises(ValueError):
            validator.parse(encoded(value), feed='bumps')
        raw = encoded(bumps_collection())
        with self.assertRaises(ValueError):
            validator.parse(raw + b' ' * (1048577 - len(raw)), feed='bumps')
        with self.assertRaises(ValueError):
            validator.parse(raw.replace(b'"source":"osm"', b'"source":"osm","source":"osm"'), feed='bumps')

    def test_bumps_revision_is_independent_and_supports_retraction(self):
        previous = bumps_collection()
        current = copy.deepcopy(previous)
        validator.validate_revision(current, previous, feed='bumps')
        current['features'] = []
        with self.assertRaises(ValueError):
            validator.validate_revision(current, previous, feed='bumps')
        current['revision'] = 2
        validator.validate_revision(current, previous, feed='bumps')
        with self.assertRaises(ValueError):
            validator.validate_revision(previous, current, feed='bumps')
        roads = collection()
        roads['revision'] = 50
        with self.assertRaises(ValueError):
            validator.validate_revision(current, roads, feed='bumps')

    def test_cli_explicit_feed_validates_bumps_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'bumps.geojson'
            original = encoded(bumps_collection())
            source.write_bytes(original)
            result = subprocess.run([sys.executable, str(Path(validator.__file__)), str(source), '--feed', 'bumps'], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(9, json.loads(result.stdout)['features'])
            self.assertEqual(original, source.read_bytes())


if __name__ == '__main__':
    unittest.main()
