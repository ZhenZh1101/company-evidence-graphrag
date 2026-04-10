from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from . import engine
from .ingest import prepare, write_json


def main():
    parser = argparse.ArgumentParser(description='上市公司文档导入、Microsoft GraphRAG 索引与可追溯问答')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'prepare', 'doctor', 'index', 'ask', 'search', 'status'):
        p = sub.add_parser(command)
        p.add_argument('--root', type=Path, required=True, help='Isolated corpus/index workspace')
        if command == 'init':
            p.add_argument('--provider', choices=list(engine.PROVIDERS), default='openclaw')
            p.add_argument('--model', help='Chat model; defaults to the provider preset')
            p.add_argument('--api-base', help='Chat API base URL; defaults to the provider endpoint')
            p.add_argument('--api-key-env', help='Environment variable containing the chat API key')
            p.add_argument('--embedding-provider', choices=['openclaw', 'openai'],
                           help='Defaults to OpenClaw for OpenClaw chat, otherwise OpenAI')
            p.add_argument('--embedding-model', help='Embedding model; defaults to the embedding provider preset')
            p.add_argument('--embedding-api-base', help='Separate embedding API base URL')
            p.add_argument('--embedding-api-key-env', help='Environment variable containing the embedding API key')
            p.add_argument('--vector-size', type=int, help='Embedding dimensions; defaults to the provider preset (3072)')
        if command == 'doctor':
            p.add_argument('--chat-only', action='store_true', help='Check chat only; embeddings are not needed for financial Q&A')
        if command == 'prepare':
            p.add_argument('--dataset', type=Path, action='append', required=True)
            p.add_argument('--profile', choices=['all', 'financial'], default='all')
            p.add_argument('--ticker')
            p.add_argument('--category', dest='categories', action='append', default=[])
            p.add_argument('--form', dest='forms', action='append', default=[])
            p.add_argument('--since')
            p.add_argument('--until')
            p.add_argument('--include-undated', action='store_true')
            p.add_argument('--limit-records', type=int)
        if command in {'ask', 'search'}:
            p.add_argument('question')
            p.add_argument('--ticker', dest='tickers', action='append', default=[])
            p.add_argument('--as-of', help='Publication cutoff; excludes unknown publication dates')
            p.add_argument('--form', dest='forms', action='append', default=[])
            p.add_argument('--fiscal-year', type=int)
            p.add_argument('--fiscal-quarter', type=int, choices=[1, 2, 3, 4])
            p.add_argument('--output', type=Path)
            if command == 'ask':
                p.add_argument('--method', choices=['financial', 'local', 'global', 'basic', 'drift'], default='financial')
                p.add_argument('--community-level', type=int, default=2)
    calculation = sub.add_parser('calculate', help='Decimal comparison of two explicitly cited values')
    calculation.add_argument('--answer', type=Path, required=True, help='Saved answer/search JSON with evidence')
    calculation.add_argument('--operands', type=Path, required=True, help='JSON object containing current and previous operands')
    calculation.add_argument('--output', type=Path)
    args = vars(parser.parse_args())
    command = args.pop('command')
    root = args['root'] = args['root'].expanduser().resolve() if 'root' in args else None
    try:
        if command == 'calculate':
            from .calculations import calculate_change
            evidence = json.loads(args['answer'].expanduser().read_text())
            operands = json.loads(args['operands'].expanduser().read_text())
            result = calculate_change(operands['current'], operands['previous'], evidence['evidence'])
            result['evidence_file'] = str(args['answer'].expanduser().resolve())
            if args['output']:
                args['output'].parent.mkdir(parents=True, exist_ok=True)
                write_json(args['output'], result)
        elif command == 'init':
            engine.initialize(**args)
            result = {'initialized': str(root)}
        elif command == 'prepare':
            args['datasets'] = args.pop('dataset')
            with engine.workspace_lock(root):
                result = prepare(**args, progress=lambda n, title: print(f'[{n}] {title}', file=sys.stderr, flush=True) if n == 1 or n % 50 == 0 else None)
            result = {**result, 'issues': f'{len(result["issues"])} entries in ingestion-report.json'}
        elif command == 'doctor':
            result = engine.doctor(**args)
        elif command == 'index':
            result = engine.build(root)
        elif command in {'ask', 'search'}:
            output = args.pop('output')
            if command == 'ask':
                result = engine.ask(**args)
            else:
                from .financial import search
                with engine.workspace_lock(root):
                    result = search(**args)
            if output:
                output = output.expanduser().resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                write_json(output, result)
                result = {'answer': result.get('answer'), 'saved': str(output), 'evidence_count': len(result['evidence'])}
        else:
            result = {name: json.loads((root / name).read_text()) if (root / name).is_file() else None
                      for name in ('ingestion-report.json', 'index-ready.json')}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Avoid dumping pydantic validation errors containing credential-bearing input.
        message = str(exc) if isinstance(exc, (ValueError, FileNotFoundError, RuntimeError)) and type(exc).__module__ == 'builtins' else f'{type(exc).__name__}; inspect workspace logs.'
        print(f'Error: {message}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
