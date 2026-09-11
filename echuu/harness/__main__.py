import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
from dotenv import load_dotenv
from .runner import run_case
from .report import build_report


def main():
    parser = argparse.ArgumentParser(description='Echuu real generation observability harness')
    parser.add_argument('--output', type=Path, default=Path('output/harness'))
    parser.add_argument('--fixtures', type=Path, default=Path(__file__).with_name('fixtures.json'))
    parser.add_argument('--variants', nargs='+', choices=['legacy_v4','refactor_no_dossier','refactor_full'], default=['legacy_v4'], help='Defaults to legacy_v4; refactor variants are opt-in experiments')
    parser.add_argument('--characters', nargs='+')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--jobs', type=int, choices=range(1,5), default=2)
    parser.add_argument('--no-audio', action='store_true')
    parser.add_argument('--no-judge', action='store_true')
    parser.add_argument('--no-references', action='store_true')
    parser.add_argument('--no-repair', action='store_true', help='Observe original output without automatic repair')
    parser.add_argument('--repair-rounds', type=int, choices=[0,1,2], default=2)
    parser.add_argument('--replay-dir', type=Path, help='Repair saved text without regenerating it')
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        print(build_report(args.output)); return
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    fixtures = json.loads(args.fixtures.read_text())
    if args.characters:
        unknown = set(args.characters)-{f['id'] for f in fixtures}
        if unknown:
            parser.error(f'unknown characters: {unknown}')
        fixtures = [f for f in fixtures if f['id'] in args.characters]
    jobs = [(f,v,None) for f in fixtures for v in args.variants]
    if args.replay_dir:
        sources = {}
        for path in args.replay_dir.glob('*/run.json'):
            source = json.loads(path.read_text())
            key = (source['character']['id'],source['variant'])
            if key in sources:
                parser.error(f'ambiguous replay source: {key}')
            sources[key] = path.parent
        missing = [(f['id'],v) for f,v,_ in jobs if (f['id'],v) not in sources]
        if missing:
            parser.error(f'missing replay sources: {missing}')
        jobs = [(f,v,sources[(f['id'],v)]) for f,v,_ in jobs]
    failed = 0
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(run_case, f, v, args.output, seed=args.seed, audio=not args.no_audio,
                               judge=not args.no_judge, references=not args.no_references, auto_repair=not args.no_repair,
                               repair_rounds=args.repair_rounds, source_run=source):(f['id'],v)
                   for f,v,source in jobs}
        for future in as_completed(pending):
            try:
                directory, result = future.result()
                failed += result['status'] != 'completed'
                print(f"HARNESS {pending[future]} {result['status']} {directory}", flush=True)
            except Exception as exc:
                failed += 1
                print(f'HARNESS worker failed: {type(exc).__name__}', flush=True)
            build_report(args.output)
    print(build_report(args.output))
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
