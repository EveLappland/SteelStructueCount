"""Original-image work packages and bounded, lossless expert context crops."""
import hashlib
import json
import math
from pathlib import Path
from PIL import Image
from .length_expert import target_rows


def freeze_context(directory,view_id,image_path,request):
    with Image.open(image_path) as image:
        width,height=image.size;box=request['bbox']
        pixels=[math.floor(box[0]*width),math.floor(box[1]*height),math.ceil(box[2]*width),math.ceil(box[3]*height)]
        digest=hashlib.sha256(Path(image_path).read_bytes()+json.dumps(pixels).encode()).hexdigest()[:20]
        name=f'length-context-{view_id}-{digest}.png'
        target=directory/'views'/name
        if not target.exists():image.crop(pixels).save(target,dpi=image.info.get('dpi',(300,300)))
    return {'image_file':name,'crop_bbox_px':pixels,'reason':request['reason']}


def work_package(item,*,previous=None,feedback=None,round=0):
    plan=item['plan'];directory=item['directory'];rows=item['rows']
    targets=target_rows(rows)
    positions={c['candidate_id']:c for child in item.get('located',[]) for c in child['observations']}
    def identity(row):
        return {'member_id':row['id'],'spec':row['spec'],'view_tags':row['view_tags'],
                'labels':[{'candidate_id':ref,'point_original_px':positions[ref]['label_point_original_px'],
                           'source_tile':positions[ref]['source_tile']} for ref in row.get('candidate_ids',[]) if ref in positions],
                'original_label_point':row.get('original_label_point')}
    metadata={'stage_one_context':item['context'],'original_size_px':[plan['width_px'],plan['height_px']],
              'grid_tiles':plan['tiles'],'target_members':[{**identity(r),'tasks':[
                  *(['RESOLVE_UNKNOWN'] if r.get('length_mm') is None else []),
                  *(['VERIFY_DIAGONAL_ENDPOINTS_AND_LEGS'] if (r.get('length_input') or {}).get('method')=='DIAGONAL' else []),
                  *(['RESOLVE_SEGMENTATION'] if r.get('segmentation_status')=='UNRESOLVED' else [])]} for r in targets],
              'context_ledger':[identity(r) for r in rows if r not in targets],'round':round,
              'independence_note':'首轮不提供旧长度、旧直角边、旧端点及位置结论，防止沿用上游错误。标签点仅为查找线索，仍须按原图确认。',
              'context_request_remaining':max(0,1-round),
              'coordinate_note':'所有endpoints和尺寸reference的点均为原图[0,1]坐标。裁图保持原像素，裁片原点见image_map。'}
    images=[{'image_number':1,'role':'original','image_file':Path(item['entry']['image_path']).name,
             'crop_bbox_px':[0,0,plan['width_px'],plan['height_px']]}]
    paths=[]
    for tile in plan['tiles']:
        if tile['crop_bbox_px']==images[0]['crop_bbox_px']:continue
        images.append({'image_number':len(images)+1,'role':'grid_detail','tile_id':tile['id'],
                       'image_file':tile['image_file'],'crop_bbox_px':tile['crop_bbox_px']})
        paths.append(str(directory/'views'/tile['image_file']))
    for crop in item.get('expert_contexts',[]):
        images.append({'image_number':len(images)+1,'role':'expanded_context',**crop})
        paths.append(str(directory/'views'/crop['image_file']))
    metadata['image_map']=images
    if previous is not None:metadata.update(previous_expert_reply=previous,local_feedback=feedback or [])
    text=(Path(__file__).parent/'prompts/length_expert.md').read_text(encoding='utf-8')
    return {**item['entry'],'detail_paths':paths},text+'\n输入工作包：\n'+json.dumps(metadata,ensure_ascii=False)
