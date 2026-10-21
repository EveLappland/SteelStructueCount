"""Run the feature UI with isolated job storage; never recover another server's DB."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8122)
    args=parser.parse_args()
    import uvicorn
    from app.main import create_app
    uvicorn.run(create_app(args.root.resolve()),host='127.0.0.1',port=args.port)
