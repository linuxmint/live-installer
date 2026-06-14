#!/usr/bin/env bash
# Provision a real NFS server on the CI runner (or any throwaway host) that
# exports a small answer file over BOTH NFSv3 and NFSv4.2, reachable via IPv4,
# IPv6, and a hostname. The tests in tests/nfs/test_nfs_real.py then fetch it
# through the installer's actual nfs:// transport with vers= pinned.
#
# Destructive: installs packages and edits /etc/exports.d, /etc/nfs.conf.d, and
# /etc/hosts. Intended for CI / disposable VMs only — do NOT run on a workstation.
set -euxo pipefail

EXPORT_DIR=/srv/li-nfs-test
TEST_HOST=li-nfs-host

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    nfs-kernel-server nfs-common rpcbind

# The answer file the tests fetch. A valid, minimal config so a future test
# could even drive an install from it.
sudo mkdir -p "$EXPORT_DIR"
sudo tee "$EXPORT_DIR/answer.yaml" >/dev/null <<'EOF'
version: 1
locale: en_US.UTF-8
timezone: America/Toronto
users:
  - name: nfsuser
    passwd: "$6$rounds=4096$salt$hashhashhashhashhashhashhash"
storage:
  target:
    match:
      first-non-removable: true
EOF
sudo chmod -R a+rX "$EXPORT_DIR"

# Export read-only to everyone on the loopback host. `insecure` allows the
# client to connect from a non-reserved port; harmless for a localhost test.
sudo mkdir -p /etc/exports.d
echo "$EXPORT_DIR *(ro,sync,no_subtree_check,insecure)" \
    | sudo tee /etc/exports.d/li-nfs.exports >/dev/null

# Explicitly enable v3 and the full v4 range so both ends of the test matrix
# are actually offered by the server (some images ship with v3 disabled).
sudo mkdir -p /etc/nfs.conf.d
sudo tee /etc/nfs.conf.d/li-nfs.conf >/dev/null <<'EOF'
[nfsd]
vers3 = y
vers4 = y
vers4.0 = y
vers4.1 = y
vers4.2 = y
EOF

# Resolve the hostname test case to loopback on both stacks.
if ! grep -q "$TEST_HOST" /etc/hosts; then
    printf '127.0.0.1 %s\n::1 %s\n' "$TEST_HOST" "$TEST_HOST" \
        | sudo tee -a /etc/hosts >/dev/null
fi

# (Re)start the services. systemd on modern runners; fall back to service(8).
sudo systemctl restart rpcbind 2>/dev/null || sudo service rpcbind restart || true
sudo systemctl restart nfs-server 2>/dev/null \
    || sudo systemctl restart nfs-kernel-server 2>/dev/null \
    || sudo service nfs-kernel-server restart
sudo exportfs -ra

# Diagnostics, so a CI failure is debuggable from the log alone.
echo "--- exportfs -v ---";        sudo exportfs -v
echo "--- rpcinfo nfs versions ---"; rpcinfo -p 2>/dev/null | grep -i nfs || true
echo "NFS server ready: $EXPORT_DIR over v3+v4.2 (IPv4/IPv6/$TEST_HOST)"
