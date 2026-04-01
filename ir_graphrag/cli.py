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
    for command in ('init', 'prepare', 'doctor', 'index', 'ask', 'status'):
        p = sub.add_parser(command)
        p.add_argument('--root', type=Path, required=True, help='Isolated corpus/index workspace')
        if command == 'init':
            p.add_argument('--model', default='openclaw/llm-gpt55')
            p.add_argument('--embedding-model', default='openclaw/llm-gpt55')
            p.add_argument('--api-base', default='http://127.0.0.1:18789/v1')
            p.add_argument('--vector-size', type=int, default=1536)
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
        if command == 'ask':
            p.add_argument('question')
            p.add_argument('--method', choices=['local', 'global', 'basic', 'drift'], default='local')
            p.add_argument('--community-level', type=int, default=2)
            p.add_argument('--output', type=Path)
    args = vars(parser.parse_args())
    command = args.pop('command')
    root = args['root'] = args['root'].expanduser().resolve()
    try:
        if command == 'init':
            engine.initialize(**args)
            result = {'initialized': str(root)}
        elif command == 'prepare':
            args['datasets'] = args.pop('dataset')
            with engine.workspace_lock(root):
                result = prepare(**args, progress=lambda n, title: print(f'[{n}] {title}', file=sys.stderr, flush=True) if n == 1 or n % 50 == 0 else None)
            result = {**result, 'issues': f'{len(result["issues"])} entries in ingestion-report.json'}
        elif command == 'doctor':
            result = engine.doctor(root)
        elif command == 'index':
            result = engine.build(root)
        elif command == 'ask':
            output = args.pop('output')
            result = engine.ask(**args)
            if output:
                output = output.expanduser().resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                write_json(output, result)
                result = {'answer': result['answer'], 'saved': str(output), 'evidence_count': len(result['evidence'])}
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
