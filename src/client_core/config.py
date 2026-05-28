"""
client_core.config
~~~~~~~~~~~~~~~~~~
ClientConfig dataclass — shared configuration object for all Tier 1 clients.
"""

from dataclasses import dataclass


@dataclass
class ClientConfig:
    prog: str            # CLI program name, e.g. "task-client"
    description: str     # argparse description
    env_file: str        # env file name, e.g. ".task.env"
    server_url_key: str  # env var for the server URL, e.g. "TASKSERVER_URL"
    api_token_key: str   # env var for the API token, e.g. "TASKSERVER_API_TOKEN"
    ca_cert_key: str     # env var for the CA cert path, e.g. "TASKSERVER_CA_CERT"
    client_name: str     # short name driving header prefix + cert dir, e.g. "task"
    version: str         # package version string
    package_name: str = ""  # pip package name, e.g. "ophix-task-client"; sent as X-Ophix-Client-Package
