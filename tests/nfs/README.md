# Real NFS transport tests

These exercise the installer's `nfs://` answer-file transport against a **live**
NFS server, across the full matrix of protocol version × address form:

| | IPv4 (`127.0.0.1`) | IPv6 (`[::1]`) | hostname (`li-nfs-host`) |
|---|---|---|---|
| **NFSv3** | ✓ | ✓ | ✓ |
| **NFSv4.2** | ✓ | ✓ | ✓ |

…plus a negotiated-default case (no `?vers=`).

They are skipped unless `LI_NFS_TEST=1` is set, because they need a provisioned
NFS server and root (`mount(2)` needs `CAP_SYS_ADMIN`). The normal unit suite
(`pytest tests/unit`) stays hermetic and rootless.

## Running

In CI this is the `nfs tests` workflow (`.github/workflows/nfs-tests.yml`). To
run on a disposable Linux host (a VM or container — **not** your workstation,
the setup script edits `/etc/exports.d`, `/etc/nfs.conf.d`, and `/etc/hosts`):

```sh
bash tests/nfs/setup-nfs-server.sh
sudo env LI_NFS_TEST=1 "$(command -v python)" -m pytest tests/nfs -v
```

The server exports a small valid `answer.yaml` read-only over both NFSv3 and
NFSv4.2, reachable on IPv4, IPv6, and the `li-nfs-host` hostname.
