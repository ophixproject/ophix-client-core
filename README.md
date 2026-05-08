# ophix-client-core

Shared base library for [Ophix Project](https://ophixproject.com) Tier 1 clients.

Provides the common lifecycle commands, HTTP utilities, and CLI builder that every domain client depends on. Domain clients (`ophix-task-client`, `ophix-cred-client`, etc.) import from here and add only their artifact-specific API calls on top.

---

## For domain client authors

Add `ophix-client-core` as a dependency in your `pyproject.toml`, then follow the pattern below.

### `_config.py` — declare your ClientConfig

```python
from client_core.config import ClientConfig
from my_client._version import __version__

CLIENT_CONFIG = ClientConfig(
    prog="my-client",
    description="Ophix My Domain client.",
    env_file=".my.env",
    server_url_key="MYSERVER_URL",
    api_token_key="MYSERVER_API_TOKEN",
    ca_cert_key="MYSERVER_CA_CERT",
    client_name="my",   # drives X-My-* request headers and .my/ cert directory
    version=__version__,
)
```

### `cli.py` — extend the base command registry

```python
from client_core.commands import build_commands
from client_core.parser import make_main
from my_client._config import CLIENT_CONFIG
from my_client.core import get_things

def cmd_fetch(args):
    ...

COMMANDS = build_commands(CLIENT_CONFIG)
COMMANDS["fetch"] = {
    "help": "Fetch things from the server.",
    "arguments": [],
    "handler": cmd_fetch,
}

main = make_main(CLIENT_CONFIG, COMMANDS)
```

`build_commands(config)` returns the full set of base commands pre-bound to your config. Extend the dict with domain-specific entries and pass it to `make_main`.

---

## What's included

### Base CLI commands

All commands are registered automatically by `build_commands(config)`.

| Command | Description |
| --- | --- |
| `quickstart <server_url> <client_name>` | Set server URL, download CA cert, and register in one step |
| `set server\|ca-cert\|token <value>` | Write a configuration value to the env file |
| `download ca-cert` | Download and save the server CA certificate |
| `register <name>` | Register this client with the server |
| `rotate-token` | Generate a new token, send to server, update env file |
| `info` | Show client identity from the server |
| `update` | Update venv info on the server |
| `doctor` | Diagnose local configuration and server connectivity |

### `client_core.config` — `ClientConfig`

Dataclass holding all domain-specific identifiers. Passed to every shared function so no global state is required.

### `client_core.core`

Shared utilities:

- `resolve_server_config(config, ...)` — load env file, resolve URL/token/CA cert, validate
- `build_client_headers(config, api_token=None)` — standard `X-{Name}-*` request headers
- `ensure_env_file(config)` — find or create the domain env file with 600 permissions
- `find_project_root()` — walk up from cwd to find project root
- `in_venv()` — return `Path(sys.prefix)` if inside a venv, else `None`
- `detect_os_flavour()` — human-readable OS description
- `detect_invocation()` — best-effort invocation string from `sys.argv`

### `client_core.parser`

- `build_parser(config, commands)` — build an `ArgumentParser` from a `ClientConfig` and `COMMANDS` dict
- `make_main(config, commands)` — return a `main()` callable suitable for use as a `pyproject.toml` entry point

---

## Environment file

Each domain client uses its own env file (e.g. `.task.env`, `.cred.env`). `ClientConfig.env_file` sets the filename. Three keys are always present:

| Key | Description |
| --- | --- |
| `{NAME}SERVER_URL` | Server base URL |
| `{NAME}SERVER_API_TOKEN` | 64-character hex client token |
| `{NAME}SERVER_CA_CERT` | Path to CA certificate PEM (optional) |

The CA certificate is saved to `.<client_name>/ca-cert.pem` relative to the project root.

---

## License

MIT
