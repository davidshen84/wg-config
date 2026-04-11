"""Tests for the wg_config CLI.

Naming convention: test_<command>_<scenario>
  - command: the CLI command under test (init, generate, ...)
  - scenario: expected behavior or condition being verified

Coverage:
  - init: config file created with valid structure (passing)
  - init: lan_access flag inserts iptables FORWARD rules (passing)
  - init: lan_access disabled by default (passing)
  - init: custom lan_if replaces default eth0 (passing)
  - init: custom lan_ip replaces default 192.168.1.0 (passing)
  - generate: server and client .conf files are created (passing)
  - generate: YAML is updated with keys after generation (passing)
  - generate: DNS entry is present in generated .conf when specified (passing)
  - generate: DNS entry is absent when not specified (passing)
  - generate: existing keys are preserved on re-generation (passing)
  - generate: new peers receive keys on next generation (passing)
"""

import os

import pytest
import yaml

from wg_config import CLI, Peers


def test_init(tmp_path):
    os.chdir(tmp_path)
    cli = CLI()
    config_path = "test_config.yaml"
    cli.init(config=config_path)
    assert os.path.exists(config_path)

    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    peers = Peers.model_validate(data)
    assert len(peers.peers) > 0


@pytest.fixture
def init_config(tmp_path):
    os.chdir(tmp_path)
    cli = CLI()
    config_path = "test_config.yaml"

    def run(**kwargs):
        if os.path.exists(config_path):
            os.remove(config_path)
        cli.init(config=config_path, **kwargs)
        with open(config_path) as f:
            return f.read()

    return run


def test_init_lan_access(init_config):
    content = init_config(lan_access=True)
    assert "iptables -A FORWARD -i %i -o eth0 -j ACCEPT" in content


def test_init_lan_access_disabled_by_default(init_config):
    content = init_config()
    assert "iptables -A FORWARD -i %i -o eth0 -j ACCEPT" not in content


def test_init_lan_if(init_config):
    content = init_config(lan_access=True, lan_if="br0")
    assert "br0" in content
    assert "eth0" not in content


def test_init_lan_ip(init_config):
    content = init_config(lan_access=True, lan_ip="10.10.0.0")
    assert "10.10.0.0/24" in content
    assert "192.168.1.0" not in content


@pytest.fixture
def generated(tmp_path):
    os.chdir(tmp_path)
    cli = CLI()
    config_path = "test_config.yaml"
    output_dir = "output"
    cli.init(config=config_path)
    cli.generate(config=config_path, output_dir=output_dir)
    return cli, config_path, output_dir


def test_generate_conf_files(generated):
    _, config_path, output_dir = generated
    assert os.path.exists(os.path.join(output_dir, "wg_server.conf"))
    assert os.path.exists(os.path.join(output_dir, "wg_client1_to_server.conf"))


def test_generate_yaml_update(generated):
    _, config_path, output_dir = generated
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    for peer in data["peers"]:
        assert "keys" in peer
        assert "public" in peer["keys"]
        assert "private" in peer["keys"]


def test_generate_keys_preserved(generated):
    cli, config_path, output_dir = generated
    with open(config_path, "r") as f:
        original_keys = {p["name"]: p["keys"] for p in yaml.safe_load(f)["peers"]}
    cli.generate(config=config_path, output_dir=output_dir)
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    for peer in data["peers"]:
        assert peer["keys"] == original_keys[peer["name"]]


def test_generate_keys_created_for_new_peer(generated):
    cli, config_path, output_dir = generated
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    data["peers"][0]["allowed_peers"].append({"name": "client2"})
    data["peers"].append(
        {
            "name": "client2",
            "addresses": ["10.0.0.3/32", "fd00:1::3/128"],
            "allowed_ips": ["0.0.0.0/0", "::0/0"],
        }
    )
    with open(config_path, "w") as f:
        yaml.safe_dump(data, f)
    cli.generate(config=config_path, output_dir=output_dir)
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    client2 = next(p for p in data["peers"] if p["name"] == "client2")
    assert "keys" in client2
    assert "public" in client2["keys"]
    assert "private" in client2["keys"]


def test_generate_dns_property(tmp_path):
    os.chdir(tmp_path)
    cli = CLI()
    config_path = "test_config.yaml"
    output_dir = "output"

    # Initialize config
    cli.init(config=config_path)

    # Load and modify config to add dns
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    # Add dns to server
    data["peers"][0]["dns"] = "1.1.1.1"
    # Add dns to client1
    data["peers"][1]["dns"] = "8.8.8.8, 8.8.4.4"

    with open(config_path, "w") as f:
        yaml.safe_dump(data, f)

    # Generate configs
    cli.generate(config=config_path, output_dir=output_dir)

    # Check server config
    server_conf = os.path.join(output_dir, "wg_server.conf")
    assert os.path.exists(server_conf)
    with open(server_conf, "r") as f:
        content = f.read()
        assert "DNS = 1.1.1.1" in content

    # Check client config
    client_conf = os.path.join(output_dir, "wg_client1_to_server.conf")
    assert os.path.exists(client_conf)
    with open(client_conf, "r") as f:
        content = f.read()
        assert "DNS = 8.8.8.8, 8.8.4.4" in content


def test_generate_no_dns_property(tmp_path):
    os.chdir(tmp_path)
    cli = CLI()
    config_path = "test_config.yaml"
    output_dir = "output"

    # Initialize config
    cli.init(config=config_path)

    # Load and modify config to remove dns (which is now in the sample)
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    for peer in data["peers"]:
        if "dns" in peer:
            del peer["dns"]
    with open(config_path, "w") as f:
        yaml.safe_dump(data, f)

    # Generate configs (no dns)
    cli.generate(config=config_path, output_dir=output_dir)

    # Check server config
    server_conf = os.path.join(output_dir, "wg_server.conf")
    with open(server_conf, "r") as f:
        content = f.read()
        assert "DNS =" not in content

    # Check client config
    client_conf = os.path.join(output_dir, "wg_client1_to_server.conf")
    with open(client_conf, "r") as f:
        content = f.read()
        assert "DNS =" not in content
