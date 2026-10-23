"""Run the label-constrained stage-two pipeline on a delivered stage-one file."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from app.label_length_workflow import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--facts',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--view',action='append',default=[])
    parser.add_argument('--api',action='store_true',help='Call the configured paid API; otherwise prepare inputs only')
    parser.add_argument('--resume',action='store_true',help='Validate identical inputs and reuse signed matching responses')
    parser.add_argument('--reasoning-effort',choices=['low','high','max'],default='high')
    parser.add_argument('--max-concurrency',type=int)
    parser.add_argument('--review-batch-size',type=int)
    args = parser.parse_args()
    load_dotenv(ROOT/'.env')
    settings = {'reasoning_effort':args.reasoning_effort}
    if args.max_concurrency is not None: settings['max_concurrency'] = args.max_concurrency
    if args.review_batch_size is not None: settings['review_batch_size'] = args.review_batch_size
    result = run(args.facts,args.output,settings,lambda text:print(text,flush=True),
                 call_api=args.api,resume=args.resume,view_ids=args.view)
    print(json.dumps(result['summary'],ensure_ascii=False),flush=True)
    print(args.output.resolve()/'index.html',flush=True)


if __name__ == '__main__': main()
