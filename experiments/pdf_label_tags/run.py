from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import Field, model_validator

from app.audit import AuditLog
from app.domain_base import Contract
from app.drawing_input import pdf_manifest, render_png
from app.native_pdf import PDF_LOCK, extract_page, write_json
from app.pipeline import model_call, parse_model_content, response_content
from app.settings import _api_settings


class Region(Contract):
    title_id: str
    kind: Literal['PLAN', 'ELEVATION']
    bbox: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode='after')
    def valid_box(self):
        a, b, c, d = self.bbox
        if not (0 <= a < c <= 1 and 0 <= b < d <= 1):
            raise ValueError('bbox must be a valid normalized page rectangle')
        return self


class Regions(Contract):
    views: list[Region]


def run(pdf, output, settings=None, emit=print, *, reuse_localization=False,
        expected_view_count=None, expected_labels_per_view=None):
    """Shared CLI/web entry; acceptance values never influence recognition."""
    settings = settings or {}
    source = Path(pdf).resolve(strict=True)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    prompt = Path(__file__).with_name('localize.md').read_text(encoding='utf-8')
    signature = {'pdf_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                 'schema': Regions.model_json_schema()}
    cache_path = output / 'localization_input.json'
    if reuse_localization:
        if not cache_path.exists() or json.loads(cache_path.read_text(encoding='utf-8')) != signature:
            raise ValueError('Saved localization does not match PDF, prompt and schema')
    elif cache_path.exists():
        raise ValueError('Output already contains a localization; use a new directory or --reuse-localization')
    else:
        write_json(cache_path, signature)
    started = time.monotonic()
    base, key, model = _api_settings(settings) if not reuse_localization else ('', '', '')
    effort = settings.get('reasoning_effort', 'high')
    audit = AuditLog(output, stage=1, secrets=(key,))
    pages, regions, calls = [], [], []
    with PDF_LOCK:
        entries = pdf_manifest([source])
    for entry in entries:
        with PDF_LOCK:
            page = extract_page(entry)
        write_json(output / 'native' / f"{entry['id']}.json", page)
        pages.append(page)
        image = output / f"{entry['id']}_page.png"
        if not image.exists():
            with PDF_LOCK:
                image.write_bytes(render_png(entry, dpi=100))
        response = output / 'responses' / f"{entry['id']}_regions.json"
        if reuse_localization:
            result = parse_model_content(response_content(json.loads(response.read_text(encoding='utf-8'))), Regions, entry)
        else:
            text = json.dumps({'page_id': page['id'], 'texts': [
                {'id': t['id'], 'text': t['text'], 'bbox': t['bbox']} for t in page['texts']]}, ensure_ascii=False)
            emit(f"Localizing {entry['file']} page {entry['page']} (API; {effort})")
            with OpenAI(base_url=base, api_key=key, timeout=600, max_retries=1) as client:
                result = model_call(client, model, prompt + '\n页面文字：\n' + text,
                    {**entry, 'image_path': str(image)}, response, Regions,
                    emit=emit, audit=audit, reasoning_effort=effort)
        raw = json.loads(response.read_text(encoding='utf-8'))
        calls.append({'response_file': str(response.relative_to(output)), 'model': raw.get('model'),
                      'usage': raw.get('usage'), 'reasoning_effort': effort,
                      'called_in_this_run': not reuse_localization})
        texts = {t['id']: t for t in page['texts']}
        seen = set()
        for index, region in enumerate(result['views'], 1):
            title = texts.get(region['title_id'])
            if not title or region['title_id'] in seen:
                raise ValueError('API title ID is absent or repeated')
            seen.add(region['title_id'])
            # A title must actually belong to its region. No silent correction.
            b, t = region['bbox'], title['bbox']
            if not (b[0] <= (t[0]+t[2])/2 <= b[2] and b[1] <= (t[1]+t[3])/2 <= b[3]):
                raise ValueError('API region does not contain its title')
            regions.append({**region, 'id': f"{entry['id']}-view{index:02d}",
                            'page_id': page['id'], 'title': title['text']})
    write_json(output / 'regions.json', regions)
    from .native import analyze
    from .report import export
    report = analyze(pages, regions)
    report.update(source=str(source), pdf_sha256=signature['pdf_sha256'],
                  page_sources=[{'id': p['id'], 'page': p['page']} for p in pages],
                  localization_calls=calls, reused_localization=reuse_localization)
    # Acceptance is deliberately downstream of every recognition operation.
    checks = []
    if expected_view_count is not None:
        checks.append({'check': 'view_count', 'expected': expected_view_count,
                       'actual': len(report['views']), 'passed': len(report['views']) == expected_view_count})
    if expected_labels_per_view is not None:
        checks.extend({'check': 'labels_per_view', 'view_id': v['id'],
                       'expected': expected_labels_per_view, 'actual': len(v['labels']),
                       'passed': len(v['labels']) == expected_labels_per_view} for v in report['views'])
    report['acceptance'] = checks
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    with PDF_LOCK:
        export(output, source, report)
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    write_json(output / 'report.json', report)
    from app.label_facts import deliver
    deliver(output, report, pages)
    audit.event('EXPERIMENT_COMPLETED', counts=report['summary'], acceptance=checks)
    emit(json.dumps(report['summary'], ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=Path('tmp/pdf_label_view_tags/run'))
    parser.add_argument('--reuse-localization', action='store_true', help='Reuse verified saved localization; no API call')
    parser.add_argument('--expected-view-count', type=int, help='Acceptance only; never sent to API')
    parser.add_argument('--expected-labels-per-view', type=int, help='Acceptance only')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    run(args.pdf, args.output, emit=lambda message: print(message, flush=True),
        reuse_localization=args.reuse_localization, expected_view_count=args.expected_view_count,
        expected_labels_per_view=args.expected_labels_per_view)
    print(args.output.resolve() / 'index.html', flush=True)
