"""Bounded source-recorded Iraq signs; source claims never authorize routing."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re

MAX_BYTES=4_000_000
KINDS={'speed_bump','speed_camera','police_checkpoint','police_station'}
TAGS={'name','name:ar','name:en','name:ku','name:ckb','ref','operator','traffic_calming',
      'amenity','police','highway','direction','maxspeed','opening_hours','check_date','survey:date','start_date','end_date'}
REQUIRED={'kind','source_id','source','osm_type','osm_id','license','geometry_origin'}
ORIGINS={'node':{'mapped_node'},'way':{'mapped_way_midpoint','mapped_area_representative_point'},
         'relation':{'mapped_area_representative_point'}}
def fields(value,required,optional=()):
    if not isinstance(value,dict) or not set(required)<=value.keys() or value.keys()-set(required)-set(optional):
        raise ValueError('Unexpected snapshot fields')
def integer(value):
    return type(value) is int and 1<=value<=9007199254740991
def validate(value):
    fields(value,{'type','name','attribution','osm_timestamp','schema_version','revision','source_sha256','features'})
    if (value['type']!='FeatureCollection' or value['name']!='Iraq source-recorded road signs'
        or value['attribution']!='© OpenStreetMap contributors, ODbL 1.0'
        or type(value['schema_version']) is not int or value['schema_version']!=1
        or not integer(value['revision']) or not isinstance(value['source_sha256'],str)
        or not re.fullmatch('[a-f0-9]{64}',value['source_sha256'])):raise ValueError('Invalid snapshot provenance')
    stamp=value['osm_timestamp']
    if not isinstance(stamp,str) or not re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z',stamp):
        raise ValueError('Invalid source date')
    datetime.strptime(stamp,'%Y-%m-%dT%H:%M:%SZ')
    features=value['features']
    if not isinstance(features,list) or len(features)>10000:raise ValueError('Too many hazard points')
    seen=set()
    for f in features:
        fields(f,{'type','id','geometry','properties'});p=f['properties'];g=f['geometry']
        fields(p,REQUIRED,TAGS);fields(g,{'type','coordinates'})
        kind=p['kind'];osm_type=p['osm_type'];oid=p['osm_id']
        if not isinstance(kind,str) or kind not in KINDS or not isinstance(osm_type,str) or osm_type not in ORIGINS or not integer(oid):
            raise ValueError('Invalid object identity')
        identity=f'osm:{osm_type}:{oid}'
        if (f['type']!='Feature' or f['id']!=identity+':'+kind or f['id'] in seen
            or p['source_id']!=identity or p['source']!='OpenStreetMap' or p['license']!='ODbL-1.0'
            or not isinstance(p['geometry_origin'],str) or p['geometry_origin'] not in ORIGINS[osm_type]):
            raise ValueError('Invalid object provenance')
        seen.add(f['id'])
        for key in p.keys()&TAGS:
            text=p[key]
            if not isinstance(text,str) or len(text)>512 or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in text):
                raise ValueError('Invalid source tag text')
        xy=g['coordinates']
        if (g['type']!='Point' or not isinstance(xy,list) or len(xy)!=2
            or any(type(n) not in (int,float) or not math.isfinite(n) for n in xy)
            or not 36.5<=xy[0]<=50.5 or not 27<=xy[1]<=39):raise ValueError('Invalid Iraq point')
        semantic={'speed_bump':bool({'bump','hump','table','cushion','mini_bumps','rumble_strip'} & set(x.strip() for x in p.get('traffic_calming','').split(';'))),
                  'speed_camera':p.get('highway')=='speed_camera','police_checkpoint':p.get('police')=='checkpoint',
                  'police_station':p.get('amenity')=='police' and p.get('police')!='checkpoint'}
        if not semantic[kind]:raise ValueError('Kind does not match source tags')
    return value
def unique(pairs):
    value={}
    for k,v in pairs:
        if k in value:raise ValueError('Duplicate JSON key')
        value[k]=v
    return value
def invalid(_):raise ValueError('Nonfinite number')
def parse(raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:raise ValueError('Snapshot exceeds byte budget')
    try:return validate(json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=invalid))
    except (UnicodeError,RecursionError,OverflowError,TypeError) as exc:raise ValueError('Invalid snapshot') from exc
def read(path):
    with Path(path).open('rb') as stream:return parse(stream.read(MAX_BYTES+1))
def validate_revision(current,previous):
    validate(current);validate(previous)
    if (current['revision']<previous['revision'] or current['osm_timestamp']<previous['osm_timestamp']
        or current['revision']==previous['revision'] and current!=previous):raise ValueError('Stale or conflicting snapshot')
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input');parser.add_argument('--previous');args=parser.parse_args()
    current=read(args.input)
    if args.previous:validate_revision(current,read(args.previous))
    print(json.dumps({'valid':True,'revision':current['revision'],'features':len(current['features']),'osm_timestamp':current['osm_timestamp']}))
