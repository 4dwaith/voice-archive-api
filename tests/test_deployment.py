"""The supported unauthenticated Compose deployment publishes only to loopback."""

from pathlib import Path

import yaml


def test_compose_published_ports_remain_localhost_only():
    compose = yaml.safe_load((Path(__file__).resolve().parents[1] / "compose.yaml").read_text())
    published = [port for service in compose["services"].values() for port in service.get("ports", [])]
    assert published
    assert all(isinstance(port, str) and port.startswith("127.0.0.1:") for port in published)
