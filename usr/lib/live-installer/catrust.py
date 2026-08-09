#!/usr/bin/python3
# coding: utf-8
"""CA trust backends: install CA certificates into the installed system's
trust store.

Mirrors pkgbackend.py: the driver holds one CaTrustBackend, picked per distro
family, and calls it through the same CommandRunner chroot boundary — so it is
unit-testable with a recording runner and imports no certificate library at
load time. Only the Debian/Mint backend (update-ca-certificates) exists today;
the seam keeps RHEL-family (update-ca-trust) addable later.

This configures the SYSTEM trust store (/etc/ssl/certs), consulted by apt,
curl, and TLS clients — deliberately separate from per-connection 802.1X/EAP
trust, which lives in NetworkManager profiles.
"""

import os


class CaTrustBackend:
    def __init__(self, runner, policy, log, target="/target"):
        self.runner = runner
        self.policy = policy
        self.log = log
        self.target = target

    def apply(self, ca_certs):
        raise NotImplementedError


class DebianCaTrustBackend(CaTrustBackend):
    # Local certs dropped here are picked up by update-ca-certificates and
    # merged into /etc/ssl/certs. They MUST end in .crt or the tool ignores them.
    CERT_DIR = "/usr/local/share/ca-certificates"

    def apply(self, ca_certs):
        host_dir = self.target + self.CERT_DIR
        os.makedirs(host_dir, exist_ok=True)
        for index, cert in enumerate(ca_certs.trusted, 1):
            path = os.path.join(host_dir, "li-ca-%d.crt" % index)
            self.log(" --> Installing CA certificate: li-ca-%d.crt" % index)
            with open(path, "w") as f:
                f.write(cert if cert.endswith("\n") else cert + "\n")
            os.chmod(path, 0o644)
        if ca_certs.remove_defaults:
            # Disable every bundled (mozilla) cert in the conf, so a --fresh
            # rebuild trusts ONLY the local certs added above. Validation
            # already guarantees there is at least one.
            self.log(" --> Removing default CA certificates from the trust store")
            self.runner.chroot(
                r"sed -i 's/^\([^#!]\)/!\1/' /etc/ca-certificates.conf")
            rc = self.runner.chroot("update-ca-certificates --fresh")
        else:
            rc = self.runner.chroot("update-ca-certificates")
        if rc != 0:
            self.policy("post_install_script_failure",
                        "update-ca-certificates failed")


def get_ca_trust_backend(runner, policy, log, *, family="debian",
                         target="/target"):
    """Pick the CA-trust backend for the target distro family. apt-family only
    today; the seam is here so update-ca-trust (RHEL) can be added later."""
    if family == "debian":
        return DebianCaTrustBackend(runner, policy, log, target=target)
    raise ValueError(f"no CA-trust backend for distro family {family!r}")
