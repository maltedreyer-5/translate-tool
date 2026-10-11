# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Compare the prompt languages (translation.prompt_language "de" and "en") on
a reference set with YOUR model.

Usage (uses config/config.yaml and config/config.local.yaml for endpoint and
model):

    python tools/prompt_eval.py                      # tools/prompt_eval_data.yaml
    python tools/prompt_eval.py my_items.yaml --runs 3 --json result.json

Per prompt language it reports the mean chrF against the references (character
n-gram F-score, 0-100, higher is better), the number of structural findings
(numbers, list items, table rows, length) and the mean time per item. The
scores compare the two prompt languages on the same model and texts; they are
not an absolute quality measure. Keep the default ("de") unless "en" is
clearly better on texts that are typical for your documents.
"""
import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.llm_client import LLMClient  # noqa: E402
from src.translation.translation_engine import TableAwareTranslationEngine  # noqa: E402


def chrf(hypothesis: str, reference: str, max_n: int = 6, beta: float = 2.0) -> float:
    """chrF score (0-100): character n-gram F-score, whitespace ignored."""
    hyp = "".join(hypothesis.split())
    ref = "".join(reference.split())
    if not hyp or not ref:
        return 0.0
    precisions, recalls = [], []
    for n in range(1, max_n + 1):
        h = Counter(hyp[i:i + n] for i in range(len(hyp) - n + 1))
        r = Counter(ref[i:i + n] for i in range(len(ref) - n + 1))
        if not h or not r:
            continue
        overlap = sum((h & r).values())
        precisions.append(overlap / sum(h.values()))
        recalls.append(overlap / sum(r.values()))
    if not precisions:
        return 0.0
    p, r = statistics.mean(precisions), statistics.mean(recalls)
    if p == 0 and r == 0:
        return 0.0
    return 100 * (1 + beta ** 2) * p * r / (beta ** 2 * p + r)


async def evaluate(items, prompt_language: str, runs: int):
    config = load_config()
    config.translation.prompt_language = prompt_language
    client = LLMClient(config)
    await client.start()
    engine = TableAwareTranslationEngine(client, config)
    rows = []
    try:
        for item in items:
            for _ in range(runs):
                start = time.time()
                try:
                    answer = await client.translate_text(
                        text=item['source'].strip(),
                        source_lang=item['source_lang'],
                        target_lang=item['target_lang'],
                        style=item.get('style', 'preserve_original'),
                        formality=item.get('formality', 'formal'))
                    answer = engine._clean_llm_output(answer, item['source'])
                    error = None
                except Exception as e:  # report and continue
                    answer, error = "", f"{type(e).__name__}: {e}"
                rows.append({
                    'id': item['id'],
                    'prompt_language': prompt_language,
                    'chrf': round(chrf(answer, item['reference']), 1),
                    'findings': len(engine._structural_findings(item['source'], answer)) if answer else None,
                    'seconds': round(time.time() - start, 2),
                    'error': error,
                    'translation': answer,
                })
    finally:
        await client.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('data', nargs='?', default=str(ROOT / 'tools' / 'prompt_eval_data.yaml'))
    parser.add_argument('--runs', type=int, default=1, help='repetitions per item and prompt language')
    parser.add_argument('--json', help='write all translations and scores to this file')
    args = parser.parse_args()
    logging.disable(logging.WARNING)

    items = yaml.safe_load(Path(args.data).read_text(encoding='utf-8'))['items']
    results = []
    for language in ('de', 'en'):
        results += asyncio.run(evaluate(items, language, args.runs))

    print(f"{'item':<18}{'chrF de':>9}{'chrF en':>9}{'find. de':>10}{'find. en':>10}")
    for item in items:
        def mean(language, key):
            values = [r[key] for r in results if r['id'] == item['id']
                      and r['prompt_language'] == language and r[key] is not None]
            return statistics.mean(values) if values else float('nan')
        print(f"{item['id']:<18}{mean('de', 'chrf'):>9.1f}{mean('en', 'chrf'):>9.1f}"
              f"{mean('de', 'findings'):>10.1f}{mean('en', 'findings'):>10.1f}")
    print()
    for language in ('de', 'en'):
        rows = [r for r in results if r['prompt_language'] == language]
        ok = [r for r in rows if not r['error']]
        print(f"prompt_language={language}: chrF {statistics.mean([r['chrf'] for r in rows]):.1f}, "
              f"findings {sum(r['findings'] or 0 for r in ok)}, errors {len(rows) - len(ok)}, "
              f"{statistics.mean([r['seconds'] for r in rows]):.1f}s per item")
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f"details: {args.json}")


if __name__ == '__main__':
    main()
