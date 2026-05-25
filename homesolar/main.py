from __future__ import annotations

import argparse

from dotenv import load_dotenv
import uvicorn

from homesolar.config import load_config
from homesolar.web.app import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="homesolar collector, API, and dashboard")
    parser.add_argument("--config", default="config/local.yaml", help="Path to YAML config")
    parser.add_argument("--host", help="Override web host")
    parser.add_argument("--port", type=int, help="Override web port")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_dotenv()
    config = load_config(args.config)
    host = args.host or config.web.host
    port = args.port or config.web.port
    uvicorn.run(create_app(config), host=host, port=port)


if __name__ == "__main__":
    main()
