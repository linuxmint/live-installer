"""Real NFS transport tests: fetch an answer file through the installer's
actual nfs:// path against a live NFS server, across the full matrix of
protocol version x address form.

These need a provisioned NFS server (tests/nfs/setup-nfs-server.sh) and root
(mount(2) needs CAP_SYS_ADMIN), so they are skipped unless LI_NFS_TEST=1 is
set — which the nfs-tests CI job does after running the setup script. The
normal unit suite stays hermetic and rootless.
"""

import os

import pytest

import auto_installer

pytestmark = pytest.mark.skipif(
    os.environ.get("LI_NFS_TEST") != "1",
    reason="real NFS server not provisioned; set LI_NFS_TEST=1 "
           "(see tests/nfs/setup-nfs-server.sh)")

EXPORT = "/srv/li-nfs-test"        # must match the setup script
MARKER = "version: 1"              # a line in the exported answer.yaml

# Address forms the server is reachable by (setup adds li-nfs-host to /etc/hosts
# on both stacks). nfs:// needs an IPv6 literal bracketed.
HOSTS = ["127.0.0.1", "[::1]", "li-nfs-host"]
VERSIONS = ["3", "4.2"]


@pytest.mark.parametrize("vers", VERSIONS)
@pytest.mark.parametrize("host", HOSTS)
def test_real_nfs_fetch(host, vers):
    url = f"nfs://{host}{EXPORT}/answer.yaml?vers={vers}"
    text = auto_installer.fetch_answer_file(url, insecure=True)
    assert MARKER in text


def test_real_nfs_negotiated_default():
    # No vers= -> mount.nfs negotiates (should land on v4.2 against this server).
    url = f"nfs://127.0.0.1{EXPORT}/answer.yaml"
    text = auto_installer.fetch_answer_file(url, insecure=True)
    assert MARKER in text
