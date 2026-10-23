"""Run third-stage matching against one or more immutable stage-two directories."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from app.entity_workflow import run


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage-two',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--api',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--reasoning-effort',choices=['low','high','max'],default='high')
    p.add_argument('--max-concurrency',type=int)
    p.add_argument('--max-calls',type=int)
    args=p.parse_args(); load_dotenv(ROOT/'.env')
    settings={k:v for k,v in vars(args).items() if k in ('reasoning_effort','max_concurrency','max_calls') and v is not None}
    result=run(args.stage_two,args.output,settings,lambda x:print(x,flush=True),call_api=args.api,resume=args.resume)
    print(json.dumps(result['summary'],ensure_ascii=False),flush=True)
    print(args.output.resolve()/'index.html',flush=True)


if __name__=='__main__': main()
