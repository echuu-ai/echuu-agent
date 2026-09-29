"""Start this checkout's research backend, independent of directory name or cwd."""
from pathlib import Path
import argparse
import os
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Echuu research REST + WebSocket server')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    os.environ['SERVER_HOST'] = args.host
    os.environ['SERVER_PORT'] = str(args.port)
    # Explicit source precedence avoids importing a different editable installation.
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'echuu-web' / 'backend'))
    import uvicorn
    uvicorn.run('main:app', host=args.host, port=args.port)


if __name__ == '__main__':
    main()
