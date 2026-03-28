"""WireGuard quick configuration generator.

The generated '.conf' is only compatible with the 'wg-quick' tool.

This module takes a YAML configuration file as input, which describes WireGuard peer
setups. It then generates WireGuard `.conf` files for each peer and their allowed
connections and outputs an updated YAML configuration file that includes any newly
generated keys.
"""

import base64
import os
from typing import Any, List, Optional, Self

import fire
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from jinja2 import Environment, Template
from pydantic import BaseModel, ConfigDict, Field, model_validator

_init_config_template = """\
peers:
  - name: server
    endpoint:
      ip: '1.2.3.4' # '[2001:db8::1]'
      port: 51820
    addresses:
      - 10.0.0.1/24
      - fd00:1::1/64
    post_scripts:
      - up: iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o {{ lan_if }} -j MASQUERADE
        down: iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o {{ lan_if }} -j MASQUERADE
{% if ipv6 %}
      - up: ip6tables -A FORWARD -i %i -j ACCEPT; ip6tables -A FORWARD -o {{ lan_if }} -j ACCEPT; ip6tables -t nat -A POSTROUTING -o {{ lan_if }} -j MASQUERADE
        down: ip6tables -D FORWARD -i %i -j ACCEPT; ip6tables -D FORWARD -o {{ lan_if }} -j ACCEPT; ip6tables -t nat -D POSTROUTING -o {{ lan_if }} -j MASQUERADE
{% endif %}
{% if lan_access %}
      - up: iptables -A FORWARD -i %i -o {{ lan_if }} -j ACCEPT; iptables -A FORWARD -i {{ lan_if }} -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -d {{ lan_ip }}/24 -j MASQUERADE
        down: iptables -D FORWARD -i %i -o {{ lan_if }} -j ACCEPT; iptables -D FORWARD -i {{ lan_if }} -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -s 10.0.0.0/24 -d {{ lan_ip }}/24 -j MASQUERADE
{% endif %}
    allowed_peers:
      - name: client1

  - name: client1
    addresses:
        - 10.0.0.2/32
        - fd00:1::2/128
    allowed_ips:
      - 0.0.0.0/0
      - ::0/0
"""

_peer_template = """
[Interface]
PrivateKey = {{ private_key }}
Address = {{ addresses | join(', ') }}
{% if listen_port %}
ListenPort = {{ listen_port }}
{% endif %}

{% for script in post_scripts %}
{% if script.up %}
PostUp = {{ script.up }}
{% endif %}
{% if script.down %}
PostDown = {{ script.down }}
{% endif %}
{% endfor %}

{% for peer in peers %}
# Peer {{ peer.name }}
[Peer]
{% if endpoint %}
Endpoint = {{ endpoint.ip }}:{{ endpoint.port }}
{% endif %}
PublicKey = {{ peer.keys.public }}
{% if allowed_ips %}
AllowedIPs = {{ allowed_ips | join(', ') }}
{% else %}
AllowedIPs = {{ peer.addresses | join(', ') }}
{% endif %}

{% endfor %}
""".strip()


class Keys(BaseModel):
    """Public/private key pair for a peer."""

    model_config = ConfigDict(extra="forbid")

    public: str
    private: str

    @classmethod
    def new(cls) -> Self:
        """Generate a key pair using cryptography (X25519 as used by WireGuard)."""

        def b64_private(key: x25519.X25519PrivateKey) -> str:
            raw = key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )

            return base64.standard_b64encode(raw).decode("ascii")

        def b64_public(key: x25519.X25519PublicKey) -> str:
            raw = key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )

            return base64.standard_b64encode(raw).decode("ascii")

        private = x25519.X25519PrivateKey.generate()
        public = private.public_key()

        return Keys(public=b64_public(public), private=b64_private(private))


class Endpoint(BaseModel):
    """Endpoint (ip and port) for a peer."""

    model_config = ConfigDict(extra="forbid")

    ip: str
    port: int


class PostScript(BaseModel):
    """Post-up/down command pair. Any of the fields may be omitted."""

    model_config = ConfigDict(extra="forbid")

    up: Optional[str] = None
    down: Optional[str] = None


class PeerRef(BaseModel):
    """Reference to another peer by name (used under the ` peers:` list)."""

    model_config = ConfigDict(extra="forbid")

    name: str


class Peer(BaseModel):
    """Configuration for a single peer entry in config.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    addresses: List[str]
    keys: Optional[Keys] = Field(
        default_factory=Keys.new,
        description="Public and private keys for the peer. "
        "A new pair will be generated if not provided.",
    )
    allowed_ips: List[str] = Field(
        default=["0.0.0.0/0", "::0/0"],
        description="For 'server', it is ignored; "
        "for 'client', set to the IP address that are "
        "allowed to tunnel through the VPN.",
    )
    post_scripts: List[PostScript] = []
    allowed_peers: List[PeerRef] = []

    # Optional sections present only for some peers
    endpoint: Optional[Endpoint] = None

    @model_validator(mode="after")
    def check_allowed_peers_and_endpoint(self) -> Self:
        """Check that allowed_peers and endpoint are consistent."""
        if self.allowed_peers and not self.endpoint:
            raise ValueError("If allowed_ips is not empty, endpoint must be defined.")
        return self


class Peers(BaseModel):
    """Root model for config.yaml."""

    model_config = ConfigDict(extra="forbid")

    peers: List[Peer]

    def _iter(self, *args: Any, **kwargs: Any) -> Any:
        return iter(self.peers)

    def get_peer(self, name: str) -> Peer:
        """Get a peer by name."""
        for peer in self.peers:
            if peer.name == name:
                return peer
        raise KeyError(f"Peer {name} not found")

    def get_allowed_peers(self, name: str) -> List[Peer]:
        """Get a list of allowed peers for a given peer."""
        return [self.get_peer(p.name) for p in self.get_peer(name).allowed_peers]


def load_yaml(path: str) -> Peers:
    """Load and validate the Peers model from a YAML file."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)

    return Peers.model_validate(data)


def save_yaml(path: str, peers: Peers):
    """Save the Peers model to a YAML file."""
    with open(path, "w") as f:
        yaml.safe_dump(peers.model_dump(), f, width=1 << 10)


class CLI:
    """WireGuard quick configuration generator."""

    def init(
        self,
        config: str = "config.yaml",
        ipv6: bool = False,
        lan_access: bool = False,
        lan_if: str = "eth0",
        lan_ip: str = "192.168.1.0",
    ):
        """Generate a initial configuration file.

        Args:
            config: Path to the config file to create. (default: config.yaml)
            ipv6: Add IPv6 forwarding/masquerade post scripts. (default: False)
            lan_access: Add LAN forwarding post scripts. (default: False)
            lan_if: LAN interface name used in the lan block. (default: eth0)
            lan_ip: LAN network address used in the lan block. (default: 192.168.1.0)
        """
        if os.path.exists(config):
            print(f"File {config} already exists.")
            return

        env = Environment(lstrip_blocks=True, trim_blocks=True)
        rendered = env.from_string(_init_config_template).render(
            ipv6=ipv6, lan_access=lan_access, lan_if=lan_if, lan_ip=lan_ip
        )
        with open(config, "w") as f:
            f.write(rendered)
        print(f"Sample config generated at {config}")

    def generate(self, config: str = "config.yaml", output_dir: str = "."):
        """Generate WireGuard configuration files.

        Reads peers from the config file, generates .conf files for each peer
        with allowed_peers defined, and saves any newly generated keys back to
        the config file.

        Args:
            config: Path to the YAML config file. (default: config.yaml)
            output_dir: Directory to write the generated .conf files. (default: current directory)
        """
        if not os.path.exists(config):
            print(f"Config file {config} not found. Run 'init' first.")
            return

        peers = load_yaml(config)
        template = Template(_peer_template, lstrip_blocks=True, trim_blocks=True)

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        for peer in peers.peers:
            if not peer.allowed_peers:
                continue

            rendered = template.render(
                private_key=peer.keys.private,
                addresses=peer.addresses,
                listen_port=peer.endpoint.port,
                post_scripts=peer.post_scripts,
                peers=peers.get_allowed_peers(peer.name),
            )

            filename = os.path.join(output_dir, f"wg_{peer.name}.conf")
            with open(filename, "w") as f:
                f.write(rendered)
            print(f"Generated {filename}")

            for remote_peer in peers.get_allowed_peers(peer.name):
                rendered = template.render(
                    private_key=remote_peer.keys.private,
                    addresses=remote_peer.addresses,
                    post_scripts=remote_peer.post_scripts,
                    allowed_ips=remote_peer.allowed_ips,
                    endpoint=peer.endpoint,
                    peers=[peer],
                )
                filename = os.path.join(
                    output_dir, f"wg_{remote_peer.name}_to_{peer.name}.conf"
                )
                with open(filename, "w") as f:
                    f.write(rendered)
                print(f"Generated {filename}")

        save_yaml(config, peers)
        print(f"Updated {config} with generated keys.")


def main():
    """CLI entry point."""
    fire.Fire(CLI())
