"""Lossless grid geometry. Pixels locate evidence; they never determine mm."""
import hashlib
import math
from PIL import Image
from .native_pdf import write_json


def plan_grid(width,height,*,trigger=2200,target_core=1800,overlap=240,max_tiles=64):
    if min(width,height,trigger,target_core,max_tiles)<=0 or target_core>trigger:
        raise ValueError('网格尺寸必须为正，核心边长不得大于触发阈值')
    if not 0<=overlap<target_core:raise ValueError('重叠宽度必须小于核心边长')
    columns=math.ceil(width/target_core) if width>trigger else 1
    rows=math.ceil(height/target_core) if height>trigger else 1
    if columns*rows>max_tiles:raise ValueError(f'网格数 {columns*rows} 超过 {max_tiles}，未缩图或丢弃区域')
    xs=[round(i*width/columns) for i in range(columns+1)]
    ys=[round(i*height/rows) for i in range(rows+1)]
    before=overlap//2;after=overlap-before;tiles=[]
    for row in range(rows):
        for col in range(columns):
            core=[xs[col],ys[row],xs[col+1],ys[row+1]]
            crop=[max(0,core[0]-before),max(0,core[1]-before),min(width,core[2]+after),min(height,core[3]+after)]
            tiles.append({'id':f'r{row+1:02d}-c{col+1:02d}','core_bbox_px':core,'crop_bbox_px':crop,
                          'core_in_crop_px':[core[0]-crop[0],core[1]-crop[1],core[2]-crop[0],core[3]-crop[1]],
                          'width_px':crop[2]-crop[0],'height_px':crop[3]-crop[1]})
    return {'width_px':width,'height_px':height,'columns':columns,'rows':rows,'tiles':tiles,
            'trigger_px':trigger,'target_core_px':target_core,'overlap_px':overlap}


def freeze_grid(directory,view_id,image_path):
    digest=hashlib.sha256(image_path.read_bytes()).hexdigest()
    with Image.open(image_path) as source:
        plan=plan_grid(*source.size)
        for tile in plan['tiles']:
            filename=f'grid-{digest[:16]}-{view_id}-{tile["id"]}.png'
            target=directory/'views'/filename
            if not target.exists():
                source.crop(tile['crop_bbox_px']).save(target,dpi=source.info.get('dpi',(300,300)))
            tile['image_file']=filename
    plan.update(input_sha256=digest,source_image=image_path.name,
                coordinate_definition='像素只用于标签定位与裁片对应，不用于推算毫米；裁片边界不是构件端点。')
    write_json(directory/'grids'/f'{view_id}-{digest[:16]}.json',plan)
    return plan
