"""
client_core.core
~~~~~~~~~~~~~~~~
Shared utility functions for all Tier 1 Ophix clients.
"""

import getpass
import os
import platform
import secrets
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import find_dotenv, load_dotenv, set_key

try:
    import distro
except ImportError:
    distro = None


def in_venv():
    # type: () -> Optional[Path]
    if hasattr(sys, "real_prefix") or sys.prefix != sys.base_prefix:
        return Path(sys.prefix)
    return None


def find_project_root():
    # type: () -> Path
    cwd = Path.cwd()
    venv_root = in_venv()
    if venv_root:
        return venv_root.parent
    for parent in [cwd] + list(cwd.parents):
        if (
            (parent / "requirements.txt").exists()
            or (parent / ".git").exists()
            or (parent / ".env").exists()
        ):
            return parent
    return cwd


def ensure_env_file(config):
    # type: (Any) -> Path
    """Find or create the domain env file with secure permissions (600)."""
    existing = find_dotenv(filename=config.env_file, usecwd=True)
    if existing:
        env_path = Path(existing)
        try:
            mode = env_path.stat().st_mode & 0o777
            if mode != 0o600:
                os.chmod(str(env_path), 0o600)
                print("Secured permissions on {} (600)".format(env_path))
        except Exception:
            pass
        return env_path
    project_root = find_project_root()
    env_path = project_root / config.env_file
    fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    print("Created {} with secure permissions (600)".format(env_path))
    return env_path


def detect_os_flavour():
    # type: () -> str
    system = platform.system()
    if system == "Linux" and distro:
        name = distro.name(pretty=True)
        version = distro.version(best=True)
        if name and version:
            return "{} {}".format(name, version)
        return name or "Linux"
    if system == "Darwin":
        return "macOS {}".format(platform.mac_ver()[0])
    if system == "Windows":
        return "Windows {}".format(platform.release())
    return system


def detect_invocation():
    # type: () -> str
    if sys.argv:
        return " ".join(sys.argv[:2])
    return "unknown"


def build_client_headers(config, api_token=None):
    # type: (Any, Optional[str]) -> Dict[str, str]
    """Build standard request headers for a domain client.

    Header prefix is derived from client_name: "task" → "X-Task-*".
    """
    prefix = "X-{}".format(config.client_name.capitalize())
    headers = {
        "{}-Client-Version".format(prefix): config.version,
        "{}-Python-Version".format(prefix): "{}.{}.{}".format(
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        ),
        "{}-OS-Type".format(prefix): platform.system().lower(),
        "{}-OS".format(prefix): detect_os_flavour(),
        "{}-User".format(prefix): getpass.getuser(),
        "{}-Invocation".format(prefix): detect_invocation(),
    }
    venv_path = in_venv()
    if venv_path:
        headers["{}-Venv-Name".format(prefix)] = venv_path.name
    if api_token:
        headers["Authorization"] = "Token {}".format(api_token)
    return headers


# ---------------------------------------------------------------------------
# Automatic token rotation
# ---------------------------------------------------------------------------

_active_config = None  # type: Any


def set_active_config(config):
    # type: (Any) -> None
    """Register the active ClientConfig for automatic token rotation."""
    global _active_config
    _active_config = config


def _auto_rotate_token(config):
    # type: (Any) -> None
    """Perform token rotation triggered by a server signal. Silent on success, warns on failure."""
    global _active_config
    saved = _active_config
    _active_config = None  # prevent re-entry if rotation calls trigger the signal again
    try:
        try:
            server_url, old_token, ca_cert, env_path_str = resolve_server_config(
                config, return_env_path=True
            )
        except SystemExit:
            print("Auto-rotation: config error. Run 'rotate-token' manually.", file=sys.stderr)
            return

        new_token = secrets.token_hex(32)
        verify = ca_cert if ca_cert else True
        headers = build_client_headers(config, api_token=old_token)

        try:
            resp = api_post(
                "{}/api/client/self/rotate-token/".format(server_url.rstrip("/")),
                headers=headers,
                json={"new_token": new_token},
                verify=verify,
            )
            resp.raise_for_status()
        except Exception as e:
            print("Auto-rotation failed: {}. Run 'rotate-token' manually.".format(e), file=sys.stderr)
            return

        try:
            test_resp = api_get(
                "{}/api/client/self/".format(server_url.rstrip("/")),
                headers=build_client_headers(config, api_token=new_token),
                verify=verify,
            )
            if test_resp.status_code != 200:
                raise Exception("validation returned {}".format(test_resp.status_code))
        except Exception as e:
            print("Auto-rotation: new token failed validation: {}. Run 'rotate-token' manually.".format(e), file=sys.stderr)
            return

        set_key(str(env_path_str), config.api_token_key, new_token)
        print("Token rotated automatically ({}).".format(config.client_name), file=sys.stderr)
    finally:
        _active_config = saved


def check_rotation_signal(response):
    # type: (Any) -> bool
    """Check response headers and rotate automatically if configured to do so."""
    required = response.headers.get("X-Token-Rotation-Required", "").lower() == "true"
    warning = response.headers.get("X-Token-Rotation-Warning", "").lower() == "true"

    if not (required or warning):
        return False

    if _active_config is not None:
        rotate_on_required = os.getenv("ROTATE_ON_REQUIRED", "true").lower() != "false"
        rotate_on_warning = os.getenv("ROTATE_ON_WARNING", "true").lower() != "false"

        if (required and rotate_on_required) or (warning and rotate_on_warning):
            _auto_rotate_token(_active_config)
            return True

    if required:
        print("WARNING: server has requested token rotation. Run 'rotate-token'.", file=sys.stderr)
    else:
        print("WARNING: token approaching rotation deadline. Run 'rotate-token'.", file=sys.stderr)
    return True


def _api_request(method, url, **kwargs):
    # type: (str, str, ...) -> Any
    """Make an HTTP request and check for the rotation signal header."""
    resp = requests.request(method, url, **kwargs)
    check_rotation_signal(resp)
    return resp


def api_get(url, **kwargs):
    # type: (str, ...) -> Any
    """GET with automatic rotation signal check. Drop-in for requests.get()."""
    return _api_request("GET", url, **kwargs)


def api_post(url, **kwargs):
    # type: (str, ...) -> Any
    """POST with automatic rotation signal check. Drop-in for requests.post()."""
    return _api_request("POST", url, **kwargs)


def api_patch(url, **kwargs):
    # type: (str, ...) -> Any
    """PATCH with automatic rotation signal check. Drop-in for requests.patch()."""
    return _api_request("PATCH", url, **kwargs)


def api_put(url, **kwargs):
    # type: (str, ...) -> Any
    """PUT with automatic rotation signal check. Drop-in for requests.put()."""
    return _api_request("PUT", url, **kwargs)


def api_delete(url, **kwargs):
    # type: (str, ...) -> Any
    """DELETE with automatic rotation signal check. Drop-in for requests.delete()."""
    return _api_request("DELETE", url, **kwargs)


def resolve_server_config(
    config,
    server_url=None,           # type: Optional[str]
    api_token=None,            # type: Optional[str]
    ca_cert=None,              # type: Optional[str]
    return_env_path=False,     # type: bool
    ignore_missing_keys=None,  # type: Optional[List[str]]
):
    # type: (...) -> tuple
    """
    Resolve server configuration from arguments, the domain env file, or the process environment.

    Resolution order: explicit arguments → env file → os.getenv.

    Keys listed in ignore_missing_keys skip the corresponding validation check.
    This allows e.g. downloading the CA cert before a token has been registered,
    or running doctor even when some values are absent.
    """
    if ignore_missing_keys is None:
        ignore_missing_keys = []

    env_path_found = None
    # Check the project root first — this resolves correctly when cwd differs
    # from the project directory, e.g. when called from a cron job (cwd = home dir).
    # find_project_root() returns the venv parent when running inside a venv,
    # which is where the env file lives regardless of cwd.
    project_root = find_project_root()
    candidate = project_root / config.env_file
    if candidate.exists():
        env_file_path = str(candidate)
    else:
        env_file_path = find_dotenv(filename=config.env_file, usecwd=True)
    if env_file_path:
        load_dotenv(env_file_path)
        env_path_found = env_file_path

    server_url = server_url or os.getenv(config.server_url_key)
    api_token = api_token or os.getenv(config.api_token_key)
    ca_cert = ca_cert or os.getenv(config.ca_cert_key)

    errors = []

    if (not server_url) and (config.server_url_key not in ignore_missing_keys):
        errors.append("Server URL not set ({})".format(config.server_url_key))

    if (not api_token or len(api_token) != 64) and (config.api_token_key not in ignore_missing_keys):
        errors.append(
            "API token missing or invalid ({} — must be 64 hex chars)".format(config.api_token_key)
        )

    if ca_cert:
        ca_path = Path(ca_cert)
        if not ca_path.exists():
            if config.ca_cert_key not in ignore_missing_keys:
                errors.append("CA cert file not found at {}".format(ca_cert))
            # Leave ca_cert as raw string so callers can still report the configured path
        else:
            ca_cert = str(ca_path.resolve())

    if errors:
        print("Error resolving server config:\n{}".format("\n".join(errors)))
        sys.exit(1)

    if return_env_path:
        return server_url, api_token, ca_cert, env_path_found
    return server_url, api_token, ca_cert
