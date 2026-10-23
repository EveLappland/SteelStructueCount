"""Localize views with the configured API, then parse native labels locally."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.pdf_label_tags.run import main

if __name__ == '__main__':
    main()
