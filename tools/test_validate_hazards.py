import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('hazard_validator',Path(__file__).with_name('validate_hazards.py'))
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
def sample():
    return {'type':'FeatureCollection','name':'Iraq source-recorded road signs',
        'attribution':'© OpenStreetMap contributors, ODbL 1.0','osm_timestamp':'2026-10-28T21:21:02Z',
        'schema_version':1,'revision':1,'source_sha256':'a'*64,'features':[{'type':'Feature',
        'id':'osm:node:123:speed_camera','geometry':{'type':'Point','coordinates':[44.36,33.31]},
        'properties':{'kind':'speed_camera','source_id':'osm:node:123','source':'OpenStreetMap',
        'osm_type':'node','osm_id':123,'license':'ODbL-1.0','geometry_origin':'mapped_node','highway':'speed_camera'}}]}
class HazardValidation(unittest.TestCase):
    def test_actual_identity_and_semantics(self):
        self.assertEqual(sample(),v.parse(json.dumps(sample()).encode()))
        for field,value in [('kind','police_station'),('osm_id',True),('source_id','osm:node:124'),
                            ('source','verified'),('geometry_origin','inferred'),('device_id','secret')]:
            with self.subTest(field=field):
                data=sample();data['features'][0]['properties'][field]=value
                with self.assertRaises(ValueError):v.validate(data)
    def test_strict_boundaries_and_provenance(self):
        for mutate in [lambda d:d.update(osm_timestamp='2026-02-30T00:00:00Z'),
                       lambda d:d.update(source_sha256='bad'),lambda d:d.update(revision=1.0),
                       lambda d:d['features'].append(copy.deepcopy(d['features'][0])),
                       lambda d:d['features'][0]['geometry'].update(coordinates=[99,33]),
                       lambda d:d['features'][0]['properties'].update(name='x'*513)]:
            data=sample();mutate(data)
            with self.assertRaises(ValueError):v.validate(data)
        for raw in [b'{"type":1,"type":2}',b'\xff',b' '*(v.MAX_BYTES+1)]:
            with self.assertRaises(ValueError):v.parse(raw)
    def test_revisions_retraction_and_source_cannot_go_back(self):
        before=sample();v.validate_revision(before,before)
        changed=sample();changed['features']=[]
        with self.assertRaises(ValueError):v.validate_revision(changed,before)
        changed['revision']=2;v.validate_revision(changed,before)
        changed['osm_timestamp']='2026-10-27T21:21:02Z'
        with self.assertRaises(ValueError):v.validate_revision(changed,before)
if __name__=='__main__':unittest.main()
