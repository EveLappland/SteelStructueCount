"""Compare original/colorized A/B views with identical first-round requests.

Example: python tools/experiment_label_colors.py --job JOB_ID --page f003-p001
The source job is read-only; all images and model responses go under tmp/.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
from pathlib import Path
import struct
import time

from dotenv import load_dotenv
from openai import OpenAI

from app.image_workflow import freeze_images
from app.llm_runner import _api_settings
from app.member_recognition import (ImageMemberResponse, allowed_model_tags,
    make_input, recognition_prompt, validate_rows)
from app.native_pdf import write_json
from app.pipeline import load_native, model_call
from app.stage_two_iteration import assess_rows
from app.store import JobStore


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True)
    parser.add_argument('--page',required=True)
    parser.add_argument('--output',default='tmp/label_color_experiment')
    parser.add_argument('--api',action='store_true',help='Make four paid first-round calls')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env')
    job=root/'data'/'jobs'/args.job
    output=root/args.output
    output.mkdir(parents=True,exist_ok=True)
    source=JobStore(root/'data'/'jobs_v2.sqlite3').stage(args.job,1)['data']
    views=[v for v in source['views'] if v['page_id']==args.page and v['kind']=='ELEVATION']
    if len(views)!=2:raise ValueError('Expected exactly two elevation views on the selected page')
    manifest=[e for e in json.loads((job/'pdf_manifest.json').read_text(encoding='utf-8')) if e['id']==args.page]
    write_json(output/'pdf_manifest.json',manifest)
    selected=copy.deepcopy(views)
    for view in selected:
        view.update(image_file='',image_bbox=[],detail_files=[],colorized_image_file='',
                    colorized_detail_files=[],colorized_label_audit_file='')
    freeze_images(output,{'views':selected})
    source_by_id={v['id']:v for v in selected}
    pages={p['id']:p for p in load_native(job)}
    reports=[]
    for view in selected:
        audit=json.loads((output/'views'/view['colorized_label_audit_file']).read_text(encoding='utf-8'))
        reports.append({'view_id':view['id'],'title':view['title'],
            'original':view['image_file'],'colorized':view['colorized_image_file'],
            'steel_labels':sum(a['kind']=='steel' for a in audit),
            'axis_labels':sum(a['kind']=='axis' for a in audit),
            'rotated_steel_labels':sum(a['kind']=='steel' and abs(a['direction'][1])>0.1 for a in audit),
            'uncolored_matches':[{k:a[k] for k in ('text','kind','direction')} for a in audit if a['changed_pixels']==0]})
    if args.api:
        base,key,model=_api_settings({})
        by_id={r['view_id']:r for r in reports}
        def request(view,mode):
            bundle=make_input(pages[view['page_id']],view,source)
            filename=view['colorized_image_file'] if mode=='colored' else view['image_file']
            details=view['colorized_detail_files'] if mode=='colored' else view['detail_files']
            image=output/'views'/filename
            width,height=struct.unpack('>II',image.read_bytes()[16:24])
            bundle['image']={'width_px':width,'height_px':height,'dpi':300,'format':'PNG'}
            entry={**manifest[0],'id':view['id'],'crop':view['bbox'],
                   'image_path':str(image),'image_dpi':300,
                   'detail_paths':[str(output/'views'/name) for name in details],
                   'allowed_view_tags':allowed_model_tags(bundle)}
            response_path=output/'responses'/f"{view['id']}_{mode}.json"
            started=time.monotonic()
            with OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1) as client:
                payload=model_call(client,model,recognition_prompt(bundle),entry,response_path,
                    ImageMemberResponse,reasoning_effort='high',auto_retry=False)
            rows=validate_rows(payload['members'],view,source)
            assessment=assess_rows(rows,pages[view['page_id']],view)
            raw=json.loads(response_path.read_text(encoding='utf-8'))
            return {'mode':mode,'model':model,'elapsed_seconds':round(time.monotonic()-started,2),
                'usage':raw.get('usage'),'row_count':len(rows),'member_cnt':assessment['member_cnt'],
                'label_count':assessment['label_count'],
                'coverage_differences':assessment['coverage_differences'],
                'feedback_count':len(assessment['feedback']),
                'rows':[{'spec':r['spec'],'member_cnt':r['member_cnt'],'length_mm':r['length_mm'],
                         'view_tags':r['view_tags'],'location':r['location']} for r in rows]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(request,v,mode):(v['id'],mode) for v in selected for mode in ('original','colored')}
            for future in as_completed(futures):
                view_id,mode=futures[future]
                try:result=future.result()
                except Exception as exc:result={'mode':mode,'error':str(exc)}
                by_id[view_id].setdefault('api_results',[]).append(result)
                write_json(output/'report.json',{'source_job':args.job,'page':args.page,'views':reports})
                print(view_id,mode,'ERROR' if 'error' in result else f"{result['member_cnt']} members",flush=True)
    write_json(output/'report.json',{'source_job':args.job,'page':args.page,'views':reports})
    print(output/'report.json')
    if args.api and any('error' in item for view in reports for item in view.get('api_results', [])):
        raise RuntimeError('Some model requests failed; inspect report.json and raw responses')


if __name__=='__main__':main()
