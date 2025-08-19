import os
import sys

# Allow running without a venv by loading third-party deps from a local folder
_LOCAL_DEPS = os.environ.get("MUDBOT_DEPS_DIR", "/workspace/.deps")
if os.path.isdir(_LOCAL_DEPS):
	sys.path.insert(0, _LOCAL_DEPS)

import asyncio
import argparse
import logging
from pathlib import Path
import yaml

from mudbot.bot import MudBot


def load_config(config_path: Path) -> dict:

	with config_path.open("r", encoding="utf-8") as fh:
		return yaml.safe_load(fh) or {}


async def run_bot(config_path: Path, host: str | None, port: int | None) -> None:

	config: dict = load_config(config_path)

	if host:
		config.setdefault("connection", {})["host"] = host
	if port:
		config.setdefault("connection", {})["port"] = port

	bot = MudBot(config)

	# Run bot and interactive stdin concurrently
	async def stdin_loop() -> None:
		loop = asyncio.get_running_loop()
		reader = asyncio.StreamReader()
		protocol = asyncio.StreamReaderProtocol(reader)
		await loop.connect_read_pipe(lambda: protocol, sys.stdin)
		while True:
			line = await reader.readline()
			if not line:
				break
			await bot.send_command(line.decode().rstrip("\n"))

	async with bot:
		stdin_task = asyncio.create_task(stdin_loop())
		await bot.run()
		await stdin_task


def main(argv: list[str] | None = None) -> int:

	parser = argparse.ArgumentParser(description="Simple configurable MUD bot")
	parser.add_argument("--config", type=Path, default=Path("mudbot/config.yaml"), help="Path to YAML config")
	parser.add_argument("--host", type=str, default=None, help="Override host")
	parser.add_argument("--port", type=int, default=None, help="Override port")
	parser.add_argument("--log-level", type=str, default="INFO", help="Logging level")

	args = parser.parse_args(argv)

	logging.basicConfig(
		level=getattr(logging, args.log_level.upper(), logging.INFO),
		format="%(asctime)s %(levelname)s %(name)s: %(message)s",
	)

	try:
		asyncio.run(run_bot(args.config, args.host, args.port))
		return 0
	except KeyboardInterrupt:
		return 0
	except Exception:
		logging.exception("Fatal error running MUD bot")
		return 1


if __name__ == "__main__":
	sys.exit(main())

