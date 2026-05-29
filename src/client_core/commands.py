"""
client_core.commands
~~~~~~~~~~~~~~~~~~~~
Standard CLI command implementations shared by all Tier 1 clients.

All cmd_* functions take (config, args). build_commands(config) returns a
declarative COMMANDS dict with handlers pre-bound via functools.partial.
Domain clients call build_commands(CLIENT_CONFIG) and extend the result.
"""

import functools
import os
import re
import secrets
import sys
import urllib3
from pathlib import Path
from typing import Optional

import requests
from dotenv import find_dotenv, set_key

from client_core.config import ClientConfig
from client_core.core import (
    api_delete,
    api_get,
    api_patch,
    api_post,
    api_put,
    build_client_headers,
    check_rotation_signal,
    ensure_env_file,
    find_project_root,
    in_venv,
    resolve_server_config,
    rotation_just_occurred,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _determine_repo_name():
    # type: () -> str
    """Use the venv's parent directory name as a default deployment ref."""
    venv_root = in_venv()
    if not venv_root:
        raise RuntimeError("Cannot determine deployment ref: not running inside a virtual environment.")
    return venv_root.parent.name


def _download_ca_cert(config, update=False, dest_path=None):
    # type: (ClientConfig, bool, Optional[str]) -> None
    server_url, _, ca_cert_existing, env_path_str = resolve_server_config(
        config,
        return_env_path=True,
        ignore_missing_keys=[config.api_token_key],
    )

    if update:
        if not ca_cert_existing or not Path(ca_cert_existing).exists():
            print("Error: no existing CA certificate found. "
                  "Use 'download ca-cert' for first-time install.")
            sys.exit(1)
        verify = ca_cert_existing
        cert_path = Path(ca_cert_existing)
    else:
        if dest_path is None and ca_cert_existing and Path(ca_cert_existing).exists():
            print("Warning: {} already points to an existing file ({}).".format(
                config.ca_cert_key, ca_cert_existing))
            print("Use '--update' to replace an existing CA certificate.")
            sys.exit(1)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        verify = False
        cert_path = Path(dest_path) if dest_path is not None else None

    url = "{}/api/server/ca-cert/".format(server_url.rstrip("/"))
    headers = build_client_headers(config)

    try:
        resp = requests.get(url, headers=headers, verify=verify)
        resp.raise_for_status()
    except requests.RequestException as e:
        print("Failed to download CA certificate from {}: {}".format(url, e))
        sys.exit(1)

    if cert_path is None:
        filename = "ca-cert.pem"
        cd = resp.headers.get("Content-Disposition")
        if cd:
            m = re.search(r'filename="?([^"]+)"?', cd)
            if m:
                filename = m.group(1)
        project_root = find_project_root()
        cert_dir = project_root / ".{}".format(config.client_name)
        cert_dir.mkdir(exist_ok=True)
        cert_path = cert_dir / filename

    with open(str(cert_path), "wb") as f:
        f.write(resp.content)

    if update:
        print("CA certificate updated at: {}".format(cert_path))
    else:
        env_path = Path(env_path_str) if env_path_str else ensure_env_file(config)
        set_key(str(env_path), config.ca_cert_key, str(cert_path))
        print("CA certificate saved to: {}".format(cert_path))
        print("{} updated with {}={}".format(config.env_file, config.ca_cert_key, cert_path))


def _register_client(config, name, deployment_ref=None):
    # type: (ClientConfig, str, Optional[str]) -> None
    """Core registration logic shared by cmd_register and cmd_quickstart."""
    server_url, existing_token, ca_cert, env_path_str = resolve_server_config(
        config,
        return_env_path=True,
        ignore_missing_keys=[config.api_token_key],
    )

    if existing_token and len(existing_token) == 64:
        print("Error: {} already present in {}. Registration should only happen once.".format(
            config.api_token_key, config.env_file))
        sys.exit(1)

    venv_path = in_venv()
    if not venv_path:
        print("Error: Cannot detect virtual environment.")
        sys.exit(1)

    if not deployment_ref:
        try:
            deployment_ref = _determine_repo_name()
        except RuntimeError:
            deployment_ref = name

    payload = {
        "name": name,
        "deployment_ref": deployment_ref,
        "venv_name": venv_path.name,
        "venv_path": str(venv_path.resolve()),
    }
    headers = build_client_headers(config)

    try:
        resp = requests.post(
            "{}/api/register/".format(server_url.rstrip("/")),
            json=payload,
            headers=headers,
            verify=ca_cert if ca_cert else True,
        )
        resp.raise_for_status()
    except requests.HTTPError:
        try:
            err_json = resp.json()
            server_msg = err_json.get("detail") or err_json.get("error") or str(err_json)
        except Exception:
            server_msg = resp.text.strip() or "No server message"
        print("\nFailed to register client")
        print("HTTP Status: {}".format(resp.status_code))
        print("Server Response: {}".format(server_msg))
        sys.exit(1)
    except requests.RequestException as e:
        print("\nFailed to register client — network error: {}".format(e))
        sys.exit(1)

    data = resp.json()
    api_token = data.get("api_token")
    if not api_token:
        print("Error: server did not return an API token.")
        sys.exit(1)

    env_path = Path(env_path_str) if env_path_str else ensure_env_file(config)
    set_key(str(env_path), config.api_token_key, api_token)

    print("Client registered successfully!")
    print("Host:           {}".format(data.get("host")))
    print("Name:           {}".format(data.get("name")))
    print("Deployment ref: {}".format(data.get("deployment_ref")))
    print("Token saved to {}".format(env_path))


# ---------------------------------------------------------------------------
# Command implementations — all take (config, args)
# ---------------------------------------------------------------------------

def cmd_quickstart(config, args):
    print("{} quickstart\n".format(config.prog))

    env_path = ensure_env_file(config)
    set_key(str(env_path), config.server_url_key, args.server_url)
    print("Server URL saved.")

    print("Installing CA certificate...")
    _download_ca_cert(config)

    print("Registering client...")
    _register_client(config, args.client_name, getattr(args, "deployment_ref", None))

    print("\nQuickstart completed successfully.")


def cmd_set(config, args):
    env_path = ensure_env_file(config)
    key_map = {
        "server":   config.server_url_key,
        "ca-cert":  config.ca_cert_key,
        "token":    config.api_token_key,
    }
    env_key = key_map[args.variable]
    set_key(str(env_path), env_key, args.value)
    print("Set {} in {} to {}".format(env_key, config.env_file, args.value))


def cmd_download(config, args):
    if args.resource == "ca-cert":
        _download_ca_cert(config, update=getattr(args, "update", False), dest_path=getattr(args, "out", None))
    else:
        print("Unknown resource '{}'. Valid resources: ca-cert".format(args.resource))
        sys.exit(1)


def cmd_register(config, args):
    _register_client(config, args.name, getattr(args, "deployment_ref", None))


def cmd_rotate_token(config, args):
    server_url, old_token, ca_cert, env_path_str = resolve_server_config(
        config, return_env_path=True,
    )

    new_token = secrets.token_hex(32)
    verify = ca_cert if ca_cert else True

    # Step 1: send the new token to the server
    headers = build_client_headers(config, api_token=old_token)
    url = "{}/api/client/self/rotate-token/".format(server_url.rstrip("/"))
    try:
        resp = api_post(
            url, headers=headers, json={"new_token": new_token}, verify=verify,
        )
        resp.raise_for_status()
    except requests.HTTPError:
        try:
            err_json = resp.json()
            server_msg = err_json.get("detail") or err_json.get("error") or str(err_json)
        except Exception:
            server_msg = resp.text.strip() or "No server message"
        print("\nFailed to rotate API token. Server returned: {}".format(server_msg))
        sys.exit(1)
    except requests.RequestException as e:
        print("\nFailed to rotate API token — network error: {}".format(e))
        sys.exit(1)

    # Step 2: validate the new token before writing it locally
    test_headers = build_client_headers(config, api_token=new_token)
    test_url = "{}/api/client/self/".format(server_url.rstrip("/"))
    try:
        test_resp = api_get(test_url, headers=test_headers, verify=verify)
    except requests.RequestException as e:
        print("\nToken sent but validation call failed (network error): {}".format(e))
        print("Local env file NOT updated. Old token may still be active.")
        sys.exit(1)

    if test_resp.status_code != 200:
        print("\nToken rotation failed: new token validation returned {}.".format(
            test_resp.status_code
        ))
        print("Local env file NOT updated. Old token is still active.")
        sys.exit(1)

    # Step 3: only now persist the new token
    set_key(str(env_path_str), config.api_token_key, new_token)
    print("API token rotated and validated. {} updated.".format(env_path_str))


def cmd_info(config, args):
    server_url, api_token, ca_cert = resolve_server_config(config)
    headers = build_client_headers(config, api_token=api_token)
    url = "{}/api/client/self/".format(server_url.rstrip("/"))

    try:
        resp = api_get(url, headers=headers, verify=ca_cert or True)
        resp.raise_for_status()
    except requests.HTTPError as e:
        print("Failed to fetch client info: {}".format(e))
        sys.exit(1)
    except requests.RequestException as e:
        print("Network error: {}".format(e))
        sys.exit(1)

    if rotation_just_occurred():
        _, api_token, _ = resolve_server_config(config)
        try:
            fresh = api_get(url, headers=build_client_headers(config, api_token=api_token), verify=ca_cert or True)
            if fresh.status_code == 200:
                resp = fresh
        except Exception:
            pass

    data = resp.json()
    print("Client name:         {}".format(data.get("name")))
    print("Version:             {}".format(config.version))
    print("Venv:                {}".format(data.get("venv_path")))
    print("Ref:                 {}".format(data.get("deployment_ref")))
    print("Last token rotation: {}".format(data.get("last_token_rotation")))


def cmd_doctor(config, args):
    print("{} doctor\n".format(config.prog))

    env_path_str = find_dotenv(filename=config.env_file, usecwd=True)

    if env_path_str:
        print("  ENV file: {}".format(env_path_str))
    else:
        print("  No ENV file found — expected {}".format(config.env_file))
        return

    try:
        mode = Path(env_path_str).stat().st_mode & 0o777
        if mode == 0o600:
            print("  ENV permissions: OK (600)")
        else:
            print("  ENV permissions: {} (recommended: 600)".format(oct(mode)))
    except Exception as e:
        print("  ENV permissions: could not check ({})".format(e))

    # Ignore all keys so resolve returns raw values without exiting
    server_url, api_token, ca_cert, _ = resolve_server_config(
        config,
        return_env_path=True,
        ignore_missing_keys=[config.server_url_key, config.api_token_key, config.ca_cert_key],
    )

    if server_url:
        print("  {}: {}".format(config.server_url_key, server_url))
    else:
        print("  {}: not set".format(config.server_url_key))
        return

    if api_token and len(api_token) == 64:
        print("  {}: present (64 chars)".format(config.api_token_key))
    else:
        print("  {}: missing or invalid".format(config.api_token_key))
        return

    if ca_cert:
        if Path(ca_cert).exists():
            print("  {}: {}".format(config.ca_cert_key, ca_cert))
        else:
            print("  {}: set but file missing: {}".format(config.ca_cert_key, ca_cert))
            return
    else:
        print("  {}: not set (using system trust store)".format(config.ca_cert_key))

    print("  Version: {}".format(config.version))
    print("  Python:  {}".format(sys.version.split()[0]))

    venv_path = in_venv()
    if venv_path:
        print("  Venv: {} ({})".format(venv_path.name, venv_path))
    else:
        print("  Venv: not detected")

    print("\nChecking server connectivity...")

    headers = build_client_headers(config, api_token=api_token)
    url = "{}/api/client/self/".format(server_url.rstrip("/"))

    try:
        resp = api_get(url, headers=headers, verify=ca_cert or True, timeout=5)
        resp.raise_for_status()
    except requests.exceptions.SSLError as e:
        print("  TLS error: {}".format(e))
        return
    except requests.exceptions.ConnectionError as e:
        print("  Cannot connect to server: {}".format(e))
        return
    except requests.HTTPError as e:
        print("  Server returned error: {}".format(e.response.status_code))
        try:
            print("  {}".format(e.response.json()))
        except Exception:
            print("  {}".format(e.response.text))
        return

    if rotation_just_occurred():
        _, fresh_token, _ = resolve_server_config(config)
        try:
            fresh = api_get(url, headers=build_client_headers(config, api_token=fresh_token), verify=ca_cert or True)
            if fresh.status_code == 200:
                resp = fresh
        except Exception:
            pass

    data = resp.json()
    print("  Authenticated successfully")
    print("\nClient identity:")
    print("  Name:                {}".format(data.get("name")))
    print("  Deployment ref:      {}".format(data.get("deployment_ref")))
    print("  Venv name:           {}".format(data.get("venv_name")))
    print("  Venv path:           {}".format(data.get("venv_path")))
    print("  Last token rotation: {}".format(data.get("last_token_rotation")))
    print("\nDoctor checks completed successfully.")


def cmd_update(config, args):
    server_url, api_token, ca_cert = resolve_server_config(config)

    venv_path = in_venv()
    if not venv_path:
        print("Error: Cannot detect virtual environment.")
        sys.exit(1)

    payload = {
        "venv_name": venv_path.name,
        "venv_path": str(venv_path.resolve()),
    }
    deployment_ref = getattr(args, "deployment_ref", None)
    if deployment_ref:
        payload["deployment_ref"] = deployment_ref

    headers = build_client_headers(config, api_token=api_token)
    url = "{}/api/client/self/update/".format(server_url.rstrip("/"))

    try:
        resp = api_patch(url, headers=headers, json=payload, verify=ca_cert if ca_cert else True)
        resp.raise_for_status()
    except requests.HTTPError:
        try:
            err_json = resp.json()
            server_msg = err_json.get("detail") or err_json.get("error") or str(err_json)
        except Exception:
            server_msg = resp.text.strip() or "No server message"
        print("\nFailed to update client. Server returned: {}".format(server_msg))
        sys.exit(1)
    except requests.RequestException as e:
        print("\nFailed to update client — network error: {}".format(e))
        sys.exit(1)

    print("Client updated successfully!")
    print("Virtual environment: {} ({})".format(venv_path.name, venv_path))
    if deployment_ref:
        print("Deployment ref updated to: {}".format(deployment_ref))


# ---------------------------------------------------------------------------
# Command registry factory
# ---------------------------------------------------------------------------

def build_commands(config):
    # type: (ClientConfig) -> dict
    """
    Build the base COMMANDS dict for a domain client.

    Returns a declarative dict mapping command names to specs with keys:
      help, arguments, mutually_exclusive_groups (optional), handler.

    Domain clients extend the returned dict with artifact-specific entries.
    All base handlers are pre-bound to config via functools.partial.
    """
    p = functools.partial

    return {
        "quickstart": {
            "help": "Bootstrap: set server URL, install CA cert, and register in one step",
            "arguments": [
                {"name": "server_url",  "help": "Server base URL"},
                {"name": "client_name", "help": "Name to register this client as"},
                {"name": "--deployment-ref", "dest": "deployment_ref", "required": False,
                 "help": "Deployment reference (defaults to parent directory name)"},
            ],
            "handler": p(cmd_quickstart, config),
        },

        "set": {
            "help": "Set a configuration value in {}".format(config.env_file),
            "arguments": [
                {"name": "variable", "choices": ["server", "ca-cert", "token"],
                 "help": "Which value to set"},
                {"name": "value", "help": "Value to store"},
            ],
            "handler": p(cmd_set, config),
        },

        "download": {
            "help": "Download resources from the server",
            "arguments": [
                {"name": "resource", "choices": ["ca-cert"], "help": "Resource to download"},
                {"name": "--update", "action": "store_true",
                 "help": "Replace existing CA certificate, using current cert to verify SSL"},
                {"name": "--out", "dest": "out", "default": None,
                 "help": "Save to this path instead of the default location"},
            ],
            "handler": p(cmd_download, config),
        },

        "register": {
            "help": "Register this client with the server",
            "arguments": [
                {"name": "name", "help": "Client name"},
                {"name": "deployment_ref", "nargs": "?",
                 "help": "Deployment reference (defaults to parent directory name)"},
            ],
            "handler": p(cmd_register, config),
        },

        "rotate-token": {
            "help": "Rotate the API token",
            "arguments": [],
            "handler": p(cmd_rotate_token, config),
        },

        "info": {
            "help": "Show client identity from the server",
            "arguments": [],
            "handler": p(cmd_info, config),
        },

        "doctor": {
            "help": "Diagnose local configuration and server connectivity",
            "arguments": [],
            "handler": p(cmd_doctor, config),
        },

        "update": {
            "help": "Update client venv info on the server",
            "arguments": [
                {"name": "--deployment-ref", "dest": "deployment_ref",
                 "help": "New deployment reference (omit to leave unchanged)"},
            ],
            "handler": p(cmd_update, config),
        },
    }
