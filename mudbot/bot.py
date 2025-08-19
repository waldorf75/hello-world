import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

import telnetlib3


LOGGER = logging.getLogger(__name__)


@dataclass
class Trigger:
	name: str
	pattern: re.Pattern
	send: Optional[str] = None
	cooldown_seconds: float = 0.0
	_last_fire_time: float = field(default=0.0, init=False)

	def should_fire(self, now: float) -> bool:
		if self.cooldown_seconds <= 0:
			return True
		return (now - self._last_fire_time) >= self.cooldown_seconds

	def mark_fired(self, now: float) -> None:
		self._last_fire_time = now


@dataclass
class Alias:
	pattern: re.Pattern
	replacement: str


class MudBot:
	def __init__(self, config: Dict[str, Any]) -> None:
		self.config = config
		self.reader: Optional[telnetlib3.TelnetReader] = None
		self.writer: Optional[telnetlib3.TelnetWriter] = None
		self._connected_event = asyncio.Event()
		self._incoming_task: Optional[asyncio.Task] = None
		self._outgoing_queue: "asyncio.Queue[str]" = asyncio.Queue()
		self._rate_limit_per_sec: float = float(
			self.config.get("behavior", {}).get("rate_limit_cmds_per_sec", 5)
		)
		self._min_cmd_interval = 1.0 / max(self._rate_limit_per_sec, 0.01)
		self._last_cmd_time = 0.0
		self._triggers: list[Trigger] = self._build_triggers()
		self._aliases: list[Alias] = self._build_aliases()
		self._login_sequence: list[dict[str, str]] = list(
			self.config.get("login_sequence", [])
		)
		self._buffer = ""

	def _build_triggers(self) -> list[Trigger]:
		triggers_cfg: Iterable[dict[str, Any]] = self.config.get("triggers", [])
		triggers: list[Trigger] = []
		for item in triggers_cfg:
			compiled = re.compile(item["pattern"], re.MULTILINE)
			triggers.append(
				Trigger(
					name=item.get("name", item["pattern"]),
					pattern=compiled,
					send=item.get("send"),
					cooldown_seconds=float(item.get("cooldown_seconds", 0.0)),
				)
			)
		return triggers

	def _build_aliases(self) -> list[Alias]:
		aliases_cfg: Iterable[dict[str, Any]] = self.config.get("aliases", [])
		aliases: list[Alias] = []
		for item in aliases_cfg:
			aliases.append(
				Alias(pattern=re.compile(item["pattern"]), replacement=item["replacement"])
			)
		return aliases

	async def __aenter__(self) -> "MudBot":
		await self.connect()
		return self

	async def __aexit__(self, exc_type, exc, tb) -> None:
		await self.close()

	async def connect(self) -> None:
		conn_cfg = self.config.get("connection", {})
		host: str = conn_cfg.get("host", "localhost")
		port: int = int(conn_cfg.get("port", 23))
		encoding: str = conn_cfg.get("encoding", "utf8")
		readminwait = float(conn_cfg.get("read_min_wait_seconds", 0.01))
		writeminwait = float(conn_cfg.get("write_min_wait_seconds", 0.01))

		LOGGER.info("Connecting to %s:%s ...", host, port)
		reader, writer = await telnetlib3.open_connection(
			host=host,
			port=port,
			encoding=encoding,
			connect_minwait=readminwait,
			writer_minwait=writeminwait,
			connect_timeout=float(conn_cfg.get("connect_timeout_seconds", 15.0)),
		)
		self.reader, self.writer = reader, writer
		self._connected_event.set()
		self._incoming_task = asyncio.create_task(self._incoming_loop())
		asyncio.create_task(self._outgoing_loop())

	async def close(self) -> None:
		try:
			if self.writer is not None:
				try:
					self.writer.close()
					await self.writer.wait_closed()
				except Exception:
					LOGGER.debug("Ignoring error on close", exc_info=True)
		finally:
			self.writer = None
			self.reader = None
			if self._incoming_task:
				self._incoming_task.cancel()
				self._incoming_task = None

	async def run(self) -> None:
		await self._connected_event.wait()
		if self._login_sequence:
			asyncio.create_task(self._run_login_sequence())
		await self._incoming_task

	async def send_command(self, command: str) -> None:
		# Apply aliases
		for alias in self._aliases:
			if alias.pattern.fullmatch(command):
				command = alias.pattern.sub(alias.replacement, command)
				break
		await self._outgoing_queue.put(command)

	async def _outgoing_loop(self) -> None:
		while True:
			command = await self._outgoing_queue.get()
			if self.writer is None:
				continue
			# Simple rate limiting
			now = time.monotonic()
			wait_for = self._min_cmd_interval - (now - self._last_cmd_time)
			if wait_for > 0:
				await asyncio.sleep(wait_for)
			self.writer.write(command + "\r\n")
			await self.writer.drain()
			self._last_cmd_time = time.monotonic()
			LOGGER.debug("TX: %s", command)

	async def _incoming_loop(self) -> None:
		assert self.reader is not None
		while True:
			data = await self.reader.read(1024)
			if not data:
				break
			self._handle_incoming_text(data)

	def _handle_incoming_text(self, data: str) -> None:
		print(data, end="", flush=True)
		self._buffer += data
		# Evaluate triggers on new text chunks
		self._evaluate_triggers(data)

	def _evaluate_triggers(self, chunk: str) -> None:
		variables = self._make_template_variables()
		now = time.monotonic()
		for trig in self._triggers:
			for match in trig.pattern.finditer(chunk):
				if not trig.should_fire(now):
					continue
				if trig.send:
					fmt_vars = dict(variables)
					fmt_vars.update(match.groupdict())
					to_send = trig.send.format_map(Default(dict(fmt_vars)))
					asyncio.create_task(self.send_command(to_send))
				trig.mark_fired(now)

	async def _run_login_sequence(self) -> None:
		# The login sequence is a list of steps with fields: expect (regex), send (template)
		if not self._login_sequence:
			return
		LOGGER.info("Executing login sequence ...")
		remaining = deque(self._login_sequence)
		while remaining:
			step = remaining[0]
			expect_pat = re.compile(step.get("expect", ".*"))
			to_send = step.get("send", "")

			# Wait for expect to appear in buffer
			await self._wait_for_pattern(expect_pat, timeout_seconds=float(step.get("timeout_seconds", 20.0)))
			if to_send:
				variables = self._make_template_variables()
				formatted = to_send.format_map(Default(variables))
				await self.send_command(formatted)
			remaining.popleft()
		LOGGER.info("Login sequence complete")

	async def _wait_for_pattern(self, pattern: re.Pattern, timeout_seconds: float = 20.0) -> None:
		end_time = time.monotonic() + timeout_seconds
		while time.monotonic() < end_time:
			if pattern.search(self._buffer):
				return
			await asyncio.sleep(0.05)
		raise asyncio.TimeoutError(f"Timeout waiting for pattern: {pattern.pattern}")

	def _make_template_variables(self) -> dict[str, Any]:
		conn_cfg = self.config.get("connection", {})
		login_cfg = self.config.get("login", {})
		return {
			"host": conn_cfg.get("host"),
			"port": conn_cfg.get("port"),
			"username": login_cfg.get("username"),
			"password": login_cfg.get("password"),
		}


class Default(dict):
	"""dict that returns empty string for missing keys when formatting"""

	def __missing__(self, key):  # type: ignore[override]
		return ""

