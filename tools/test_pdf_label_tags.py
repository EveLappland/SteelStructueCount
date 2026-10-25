"""Localize views with the configured API, then parse native labels locally."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from app.label_catalog.run import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=Path('tmp/pdf_label_view_tags/run'))
    parser.add_argument('--reuse-localization', action='store_true', help='Reuse verified saved localization; no API call')
    parser.add_argument('--expected-view-count', type=int, help='Acceptance only; never sent to API')
    parser.add_argument('--expected-labels-per-view', type=int, help='Acceptance only')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    run(args.pdf, args.output, emit=lambda message: print(message, flush=True),
        reuse_localization=args.reuse_localization, expected_view_count=args.expected_view_count,
        expected_labels_per_view=args.expected_labels_per_view)
    print(args.output.resolve() / 'index.html', flush=True)


if __name__ == '__main__':
    main()
