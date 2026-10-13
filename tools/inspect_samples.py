"""Read-only source inspection and a explicitly unreviewed acceptance worksheet."""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.native_pdf import extract_page, write_json


def inspect(folder,output):
    pages=[]
    for index in range(1,6):
        path=folder/f'4422-AI-DU-302003PR01SA-0{index}_00.pdf'
        native=extract_page({'id':f'f{index-1:03d}-p001','source_path':str(path),'page':1,'file':path.name})
        pages.append(native)
    titles=[{'page_id':p['id'],**t} for p in pages for t in p['titles']]
    levels=[]
    for page in pages:
        for text in page['texts']:
            for m in re.finditer(r'EL\.\s*(\d+\.\d+)\((TOS|BOB)\)',text['text']):
                levels.append({'value_m':float(m[1]),'basis':m[2],'evidence_id':text['id']})
    counts=dict(Counter(t['kind'] for t in titles))
    expected={'PLAN':5,'ELEVATION':7,'ISOMETRIC':1}
    report={'source_files':[p['file'] for p in pages],'native_view_counts':counts,
            'expected_counts':expected,'coverage_passed':counts==expected,'titles':titles,
            'levels':levels,'model_called':False,'human_member_acceptance_completed':False}
    write_json(output/'report.json',report)
    templates=[]
    for kind,title in [('straight_beam','PLAN AT EL.107.500(TOS)'),
                       ('explicit_splice','ELEVATION ON ALIGNMENT 302003-PR001I/A'),
                       ('two_angle_brace','ELEVATION ON ALIGNMENT 302003-PR001I/1')]:
        found=next(t for t in titles if t['title']==title)
        templates.append({'case':kind,'view_title':title,'page_id':found['page_id'],
                          'title_evidence_id':found['evidence_id'],'human_confirmed':False,
                          'profile':None,'start':None,'end':None,'boundary_evidence_ids':[],
                          'expected_nominal_length_mm':None,'expected_component_count':None,
                          'duplicate_observations':[],'reviewer':None,'review_notes':''})
    write_json(output/'representative_members_to_review.json',templates)
    print(f"Native view counts: {counts}; coverage passed: {counts==expected}")
    print(f"Report and unreviewed member templates: {output.resolve()}")
    return counts==expected


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('tmp/sample_review'))
    args=parser.parse_args()
    raise SystemExit(0 if inspect(args.directory,args.output) else 1)
