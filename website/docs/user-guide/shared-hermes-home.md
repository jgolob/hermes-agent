---
sidebar_position: 15
title: "Shared, Auditable Hermes Home"
description: "Let trusted operators inspect Hermes state without sharing a container UID"
---

# Shared, Auditable Hermes Home

`HERMES_SHARED_HOME=1` is a POSIX deployment mode for running Hermes as a
dedicated service account while allowing trusted human operators to inspect and
manage its state directly. It is useful for reviewing agent-created skills,
hooks, logs, memories, sessions, configuration, and credentials without
entering a container or making Hermes impersonate a host user.

The security boundary is the dedicated `hermes` group. Every member can read
and modify Hermes state, including API keys and refresh tokens. Keep membership
limited to trusted administrators and services. This access is intentional:
group members can already modify configuration, hooks, and skills that Hermes
executes, so hiding token files from the same administrative group would not
create a meaningful security boundary. If operators must review state without
being trusted with credentials, do not use this mode.

When enabled, Hermes maintains Linux directories as `2770`, ordinary files as
`0660`, executable files as `0770`, and uses umask `0007`. The Linux setgid
bit keeps new directories in the `hermes` group. On macOS, directory mode is
`0770`: macOS strips setgid from directories and uses BSD group inheritance
instead. This is a deliberate reviewable security model, not a compatibility
workaround.

`HERMES_SHARED_HOME=1` is an enforcement contract, not a one-time migration.
At every container or native process start, Hermes inspects the complete
`HERMES_HOME` tree and repairs group ownership and modes that have drifted.
This includes profiles, workspaces, lazy-installed packages, and paths added by
future releases. Correct entries are only inspected; Hermes avoids redundant
`chmod` and `chgrp` calls.

:::note Native Windows

This mode is a no-op on native Windows. Windows ACLs have no direct equivalent
to POSIX GIDs, setgid directories, `chmod g+rwX`, or `newgrp`. For this exact
shared-group model, use Linux, WSL2, or a Linux container.

:::

## 1. Create the service account and group

Run these commands once as an administrator on Debian/Ubuntu. Use the
equivalent account-management commands on another Linux distribution.

```bash
sudo groupadd --system hermes
sudo useradd \
  --system \
  --gid hermes \
  --create-home \
  --home-dir /var/lib/hermes \
  --shell /usr/sbin/nologin \
  hermes
```

Add the human operator to that group, then refresh the current shell's group
membership:

```bash
sudo usermod -aG hermes "$USER"
newgrp hermes
```

Log out and back in before relying on the new membership from existing terminal
or desktop sessions.

Inspect the identities and numeric group ID:

```bash
id hermes
id "$USER"
id -g hermes
getent group hermes
```

The numeric **GID**, not the UID, is what must match a Linux container.

## 2. Prepare or migrate the Hermes data directory

This example uses `/var/lib/hermes/.hermes` as `HERMES_HOME`:

```bash
sudo install -d -o hermes -g hermes -m 2770 /var/lib/hermes/.hermes
sudo chgrp -R hermes /var/lib/hermes/.hermes
sudo chmod -R g+rwX,o-rwx /var/lib/hermes/.hermes
sudo find /var/lib/hermes/.hermes -type d -exec chmod g+s {} +
```

`g+rwX` uses capital `X`: it grants execute permission to directories and to
files that were already executable, without making every regular file
executable.

On filesystems with POSIX ACLs, also configure default inheritance:

```bash
sudo setfacl -R -m g:hermes:rwX,m::rwX /var/lib/hermes/.hermes
sudo setfacl -R -d -m u::rwx,g::rwx,o::---,m::rwx /var/lib/hermes/.hermes
```

Verify the result as both identities:

```bash
namei -l /var/lib/hermes/.hermes
stat -c '%A %a %U:%G %n' /var/lib/hermes/.hermes
getfacl /var/lib/hermes/.hermes
sudo -u hermes touch /var/lib/hermes/.hermes/group-review-test
cat /var/lib/hermes/.hermes/group-review-test
```

## 3. Docker Compose

The numeric host-GID bind-mount model requires a Linux Docker host. Docker
Desktop on macOS and Windows virtualizes bind-mount ownership; use a Linux VM,
WSL2, or a Linux host when the host operator must share the container's group.

Do not set Compose `user:`, `HERMES_UID`, `PUID`, or `PGID` for this model.
Keep the image's dedicated `hermes` UID and align only its primary group to the
host's `hermes` GID:

```bash
export HERMES_GID="$(id -g hermes)"
```

```yaml
services:
  hermes:
    image: nousresearch/hermes-agent:latest
    command: gateway run
    volumes:
      - /var/lib/hermes/.hermes:/opt/data
    environment:
      HERMES_SHARED_HOME: "1"
      HERMES_GID: "${HERMES_GID}"

  trusted-inspector:
    image: alpine:latest
    command: sleep infinity
    volumes:
      - /var/lib/hermes/.hermes:/opt/data
    group_add:
      - "${HERMES_GID}"
```

The containers can have different UIDs. They cooperate through the matching
numeric `hermes` GID. Only give `group_add` and the mounted directory to
services trusted to read and change all Hermes state.

Container bootstrap starts as root, so each restart can repair group ownership
and permissions even when a bind-mounted file is owned by a host operator. The
file's UID is preserved; only its group and mode are normalized.

After startup, verify both access and ownership without relying on a matching
host UID:

```bash
docker compose exec --user hermes hermes id
docker compose exec hermes stat -c '%A %a %u:%g %n' /opt/data /opt/data/config.yaml
docker compose exec trusted-inspector sh -c 'id && ls -la /opt/data/skills'
```

The `--user hermes` above applies only to that diagnostic command; it does not
change the service definition or map Hermes to the host operator's UID.

## 4. Native virtualenv and systemd service

This applies when Hermes runs as a dedicated `hermes` service account. A normal
per-user virtualenv does not need shared mode because its owner can already
review all of its files.

Install Hermes as the service account. Invoking `bash` explicitly works even
though the account has `/usr/sbin/nologin` as its login shell:

```bash
sudo -u hermes -H env \
  HERMES_HOME=/var/lib/hermes/.hermes \
  HERMES_SHARED_HOME=1 \
  bash -lc 'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup'
```

Run interactive setup under the same identity and deployment environment:

```bash
sudo -u hermes -H env \
  HERMES_HOME=/var/lib/hermes/.hermes \
  HERMES_SHARED_HOME=1 \
  /var/lib/hermes/.local/bin/hermes setup
```

Install the boot-time service as root, but explicitly run it as `hermes`:

```bash
sudo env HERMES_HOME=/var/lib/hermes/.hermes \
  /var/lib/hermes/.local/bin/hermes gateway install \
  --system --run-as-user hermes --no-start-now
```

Add the shared-mode deployment settings to the installed service:

```bash
sudo systemctl edit hermes-gateway
```

```ini
[Service]
Environment="HERMES_HOME=/var/lib/hermes/.hermes"
Environment="HERMES_SHARED_HOME=1"
UMask=0007
```

Then reload and restart it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable hermes-gateway
sudo systemctl restart hermes-gateway
sudo systemctl status hermes-gateway
```

The explicit `UMask` is defense in depth; Hermes also applies `0007` when
shared-home mode is enabled.

Native enforcement runs as the Hermes process and therefore cannot chmod files
owned by another user. Keep the tree owned by the `hermes` service account;
human operators should edit through their `hermes` group membership without
taking ownership. Hermes reports any paths it cannot repair and continues
starting, so these warnings should be treated as a failed reviewability check.

Because enforcement inspects the complete state tree once per process start,
very large homes may add some startup I/O. Already-correct entries are not
rewritten.

## When not to use it

Leave `HERMES_SHARED_HOME` unset for a personal desktop install, an untrusted
multi-user machine, or any environment where group members must not access
credentials. Hermes then retains its owner-only permission defaults.

`HERMES_HOME_MODE` only changes directory modes and `HERMES_SKIP_CHMOD` only
preserves selected existing modes. Neither provides the shared-group creation
and inheritance contract of `HERMES_SHARED_HOME=1`.
