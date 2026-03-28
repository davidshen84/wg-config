# wg-config

WireGuard quick configuration generator.

WireGuard has a weak definition of *server* and *client* — in theory, any peer can connect to any other. In practice, most deployments have a single *server* peer that accepts incoming connections from multiple *client* peers. `wg-config` models this pattern: you describe your peers in a single YAML file, and it generates ready-to-use `.conf` files for each one (compatible with `wg-quick`).

Keys are generated automatically and written back into `config.yaml` on first run, so you never have to manage them manually.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- WireGuard tools (`wg-quick`) installed on the target systems

## Setup

Install dependencies into a local virtualenv (recommended for development):

```bash
uv sync
```

Install as a global tool:

```bash
uv tool install .
```

Or run without installing:

```bash
uvx --from . wg-config --help
```

## Usage

### 1. Initialize a configuration file

```bash
uv run wg-config init [--config config.yaml] [--ipv6] [--lan-access] [--lan-if eth0] [--lan-ip 192.168.1.0]
```

Creates a `config.yaml` with a sample server + one client peer. Flags:

| Flag | Default | Description |
|---|---|---|
| `--config` | `config.yaml` | Path to the config file to create |
| `--ipv6` | `False` | Add IPv6 forwarding/masquerade `PostUp`/`PostDown` rules |
| `--lan-access` | `False` | Add LAN forwarding rules so VPN clients can reach the local network |
| `--lan-if` | `eth0` | LAN-facing interface name used in the generated iptables rules |
| `--lan-ip` | `192.168.1.0` | LAN network address used in the generated iptables rules |

### 2. Edit `config.yaml`

Add or remove peers, adjust addresses, and set the server's public IP/port. Leave the `keys` field out — they will be generated on the first `generate` run.

### 3. Generate WireGuard `.conf` files

```bash
uv run wg-config generate [--config config.yaml] [--output-dir .]
```

This will:
1. Load and validate `config.yaml`.
2. Generate a `wg_<server>.conf` for each peer that has `allowed_peers` defined.
3. Generate a `wg_<client>_to_<server>.conf` for each client allowed by that server.
4. Write any newly generated keys back into `config.yaml`.

Re-running `generate` is safe: existing keys are preserved, and new keys are only generated for peers that don't have them yet.

## config.yaml reference

```yaml
peers:
  - name: server               # Unique name; used in output filenames
    endpoint:
      ip: '1.2.3.4'           # Public IP (or hostname) of this peer
      port: 51820
    addresses:
      - 10.0.0.1/24
      - fd00:1::1/64
    post_scripts:              # Optional PostUp/PostDown iptables rules
      - up: iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
        down: iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
    allowed_peers:             # Peers this server accepts connections from
      - name: client1

  - name: client1
    addresses:
      - 10.0.0.2/32
      - fd00:1::2/128
    allowed_ips:               # Traffic to route through the VPN tunnel
      - 0.0.0.0/0
      - ::0/0
```

**Key rules:**
- `name` must be unique across all peers.
- `endpoint` is required for any peer that lists `allowed_peers`.
- `keys` is optional — omit it and a fresh X25519 key pair will be generated.
- `allowed_ips` on a client controls what traffic is routed through the tunnel (`0.0.0.0/0` = all traffic).

## Development

### Pre-commit hooks

The project uses [pre-commit](https://pre-commit.com/) to run linting and formatting checks before each commit. Install the hooks after cloning:

```bash
uv run pre-commit install
```

Hooks run automatically on `git commit`. To run them manually against all files:

```bash
uv run pre-commit run --all-files
```

The configured hooks are:
- **trailing-whitespace**, **end-of-file-fixer**, **check-yaml**, **check-added-large-files** (general hygiene)
- **ruff-check** with `--fix` (lint + auto-fix)
- **ruff-format** (code formatting)

### Running tests

```bash
uv run pytest
```

With coverage:

```bash
uv run pytest --cov=wg_config
```

### Linting

```bash
uv run ruff check .
```

The project uses [ruff](https://docs.astral.sh/ruff/) with `E`, `F`, `N`, `D`, `PL`, and `I` rule sets (Google-style docstrings).
