# MUD Bot

A minimal, configurable MUD bot in Python using Telnet. Supports:

- Triggers: regex -> command
- Aliases: local input shortcuts
- Login sequence automation
- Simple rate limiting

## Quickstart

1) Install dependencies:

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

2) Configure `mudbot/config.yaml` with your MUD host, port, and credentials.

3) Run:

```bash
python main.py --log-level DEBUG
```

Type commands in the terminal; the bot will forward them to the MUD. Triggers defined in config will auto-send responses.

## Configuration

See `mudbot/config.yaml` for a documented example. You can also override host/port:

```bash
python main.py --host my.mud.org --port 4000
```

## Notes

- Triggers and login sequence support `{username}`, `{password}`, `{host}`, `{port}` templating. Triggers also include any named regex groups.
- Prompts without newlines are handled by processing raw chunks, not just lines.

# hello-world
This is my first repository
I like computers and tacos.
