"""Render the two reviewed plan regions for repeatable visual inspection; no API."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
import pymupdf as fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.drawing_input import render_png, render_jpeg


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,default=Path(os.getenv('LLM_SAMPLE_DIR',
        str(Path.home()/'Desktop/TeklaProgram/建模图纸资料/建模图纸资料'))))
    parser.add_argument('--output',type=Path,default=ROOT/'tmp'/'pdf_render_test')
    args=parser.parse_args()
    fixture=json.loads((ROOT/'tests/fixtures/plan_02_views.json').read_text(encoding='utf-8'))
    source=args.directory/fixture['file']
    args.output.mkdir(parents=True,exist_ok=True)
    result={'source':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'basis':fixture['basis'],'dpi':300,'api_calls':0,'views':[]}
    for view in fixture['views']:
        entry={'source_path':str(source),'page':fixture['page'],'crop':view['bbox']}
        png=args.output/(view['name']+'_300dpi.png')
        png.write_bytes(render_png(entry,dpi=300))
        jpeg=args.output/(view['name']+'_preview.jpg')
        jpeg.write_bytes(render_jpeg(entry,max_dimension=2600))
        pixmap=fitz.Pixmap(png.read_bytes())
        result['views'].append({**view,'png':str(png),'preview':str(jpeg),
                                'width_px':pixmap.width,'height_px':pixmap.height})
    (args.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
