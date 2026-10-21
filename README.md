# zyxel-cli

A small Python CLI for enabling or disabling an Ethernet port and its PoE
supply in one operation on a Zyxel GS1200-5HP v2.

Tested against a GS1200-5HP v2 running firmware `V2.00(ABKN.3)C0`.

The switch permits only one web-management session at a time. Log out of its
web interface before using the CLI. The tool logs out when each command ends.

Developed with AI.

## Run without installing

```sh
export ZYXEL_PASSWORD='your-password'
PYTHONPATH=src python -m zyxel_cli --host 192.168.1.3 status
PYTHONPATH=src python -m zyxel_cli --host 192.168.1.3 disable 2
PYTHONPATH=src python -m zyxel_cli --host 192.168.1.3 enable 2
```

Ports 1 through 4 are accepted because those are the PoE-capable ports on the
GS1200-5HP v2. Several ports can be changed atomically:

```sh
PYTHONPATH=src python -m zyxel_cli disable 1 3 4
```

If `ZYXEL_PASSWORD` is unset, the CLI prompts without echoing the password.
Use `--dry-run` before an enable/disable command to show the current and
proposed masks without writing them.

## Install

```sh
pipx install git+https://github.com/marrold/zyxel-cli.git
zyxelcli status
```

Both `zyxelcli` and the shorter backwards-compatible `zyxel` executable are
installed in an isolated environment. To reinstall the latest version from
GitHub:

```sh
pipx install --force git+https://github.com/marrold/zyxel-cli.git
```

## Local development

Clone the repository and install the working tree in editable mode:

```sh
git clone https://github.com/marrold/zyxel-cli.git
cd zyxel-cli
pipx install -e .
```

Changes made in the working tree are then used directly by `zyxelcli`. If an
existing installation needs to be replaced with the editable checkout, use:

```sh
pipx install --force -e .
```

## YAML configuration and named ports

The optional configuration file is `~/.zyxel-cli`. For example:

```yaml
default: workshop

switches:
  workshop:
    host: 192.168.1.3
    password: gateway1
    ports:
      RFP1: 1
      RFP2: 2
    macros:
      "enable RFP1":
        enable: RFP1
        disable: RFP2
      "enable RFP2":
        enable: RFP2
        disable: RFP1

  studio:
    host: 192.168.1.4
    password: another-password
    ports:
      MAIN: 1
      BACKUP: 2
    macros:
      "enable MAIN":
        enable: MAIN
        disable: BACKUP
```

`default` selects the switch used when `--switch` is omitted. Select another
switch by name with `--switch` or `-s`:

```sh
zyxelcli status                       # uses workshop
zyxelcli --switch studio status
zyxelcli -s studio "enable MAIN"
```

If the file contains exactly one switch, it is selected automatically even
without a `default`. The original single-switch YAML layout remains supported.

Restrict the file because it contains the switch password:

```sh
chmod 600 ~/.zyxel-cli
```

Named ports work with the normal commands:

```sh
zyxelcli disable RFP1
zyxelcli enable RFP2
```

Run either atomic swap macro using the requested shorthand:

```sh
zyxelcli "enable RFP1"
zyxelcli "enable RFP2"
```

The explicit spelling `zyxelcli run "enable RFP1"` also works. Add
`--dry-run` to preview a macro. Command-line host/password values override the
environment and configuration file; `ZYXEL_PASSWORD` overrides the configured
password.

## How it works

The firmware does not expose a documented API. Its browser UI uses:

- `POST /login.cgi` with Zyxel's password obfuscation and a `token` cookie;
- `GET /port_state_data.js` for the current port, PoE, flow-control and speed
  settings;
- `POST /port_state_set.cgi` to apply the complete set of port settings;
- `GET /logout.html` to release the switch's single management session.

The CLI reads the complete current configuration, changes both relevant bits,
posts the complete form, then reads it back. It refuses a write if it cannot
parse the settings needed to preserve unrelated port configuration.
