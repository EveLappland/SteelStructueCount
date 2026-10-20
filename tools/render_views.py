"""Render named regions from any PDF; no model API or project-specific axes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.drawing_input import render_png


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--page',type=int,default=1)
    parser.add_argument('--region',action='append',nargs=5,metavar=('NAME','LEFT','TOP','RIGHT','BOTTOM'),required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'tmp/render_views')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    rows=[];names=set()
    for name,*coordinates in args.region:
        if not name or Path(name).name!=name or name in names or name in ('.','..'):
            parser.error('区域名称必须唯一且不能包含路径')
        names.add(name)
        entry={'source_path':str(args.pdf),'page':args.page,'crop':list(map(float,coordinates))}
        image=render_png(entry,dpi=300)
        target=args.output/(name+'_300dpi.png');target.write_bytes(image)
        rows.append({'name':name,'page':args.page,'bbox':entry['crop'],'image':str(target.resolve()),
                     'sha256':hashlib.sha256(image).hexdigest()})
    (args.output/'manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
