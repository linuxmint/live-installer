#!/usr/bin/python3
# coding: utf-8
"""Render the answer file's netplan-v2-shaped ``network:`` section into
NetworkManager keyfiles for the installed system.

We deliberately do NOT shell out to netplan: LMDE/Debian does not ship it,
and Mint/LMDE installed systems are managed by NetworkManager, whose keyfile
format (``/etc/NetworkManager/system-connections/<id>.nmconnection``, mode
0600) is the natural target. This mirrors how cloud-init renders its own
Network Config v2 to the NetworkManager backend on Debian-family systems.

The single public entry point, :func:`render`, is pure: it takes a validated
:class:`schema.Network` (or any object with the same attributes) and returns
an ordered ``{filename: content}`` dict. The caller writes each file with
mode 0600. Keeping it side-effect-free makes it trivially unit-testable.
"""

import ipaddress
import uuid

# Stable namespace so a given interface id always renders to the same UUID.
# VLANs reference their parent by this UUID, which is robust even when the
# parent binds by MAC and has no fixed interface name.
_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _conn_uuid(iface_id):
    return str(uuid.uuid5(_NAMESPACE, iface_id))


def _split_family(addresses):
    v4 = [a for a in addresses if ":" not in a]
    v6 = [a for a in addresses if ":" in a]
    return v4, v6


def _ip_section(family, cfg):
    """Build one ``[ipv4]``/``[ipv6]`` keyfile section as a list of lines.

    family is 4 or 6.  cfg is the interface's IP config (dhcp4/dhcp6,
    addresses, gateway4/gateway6, nameservers, routes).
    """
    is_v4 = family == 4
    header = "[ipv4]" if is_v4 else "[ipv6]"
    v4_addrs, v6_addrs = _split_family(cfg.addresses)
    addrs = v4_addrs if is_v4 else v6_addrs
    dhcp = cfg.dhcp4 if is_v4 else cfg.dhcp6
    gateway = cfg.gateway4 if is_v4 else cfg.gateway6

    if dhcp:
        method = "auto"
    elif addrs:
        method = "manual"
    else:
        # No DHCP and no static address for this family. IPv4 is switched
        # off entirely; IPv6 keeps link-local only (predictable: no global
        # SLAAC address unless the admin opts in with dhcp6/addresses).
        method = "disabled" if is_v4 else "link-local"

    lines = [header, "method=%s" % method]

    if method == "manual":
        for i, addr in enumerate(addrs, 1):
            lines.append("address%d=%s" % (i, addr))
        if gateway:
            lines.append("gateway=%s" % gateway)

    # DNS for this family is dropped onto whichever section is active.
    if method != "disabled":
        dns = [a for a in cfg.nameservers.addresses
               if (ipaddress.ip_address(a).version == family)]
        if dns:
            lines.append("dns=%s;" % ";".join(dns))
        if cfg.nameservers.search:
            lines.append("dns-search=%s;" % ";".join(cfg.nameservers.search))

    # Extra routes for this family (the default route is normally expressed
    # via gateway4/gateway6 above, but routes: [{to: default, ...}] also works).
    route_idx = 0
    for route in cfg.routes:
        if route.to == "default":
            dest = "0.0.0.0/0" if is_v4 else "::/0"
            via_v = ipaddress.ip_address(route.via).version
            if via_v != family:
                continue
        else:
            if ipaddress.ip_network(route.to, strict=False).version != family:
                continue
            dest = route.to
        route_idx += 1
        value = "%s,%s" % (dest, route.via)
        if route.metric is not None:
            value += ",%d" % route.metric
        lines.append("route%d=%s" % (route_idx, value))

    return lines


def _ethernet_lines(iface_id, cfg):
    lines = ["[connection]",
             "id=%s" % iface_id,
             "uuid=%s" % _conn_uuid(iface_id),
             "type=ethernet"]
    eth_section = []
    match = getattr(cfg, "match", None)
    if match is not None and match.macaddress:
        # Bind by hardware address; survives kernel interface renaming.
        eth_section.append("[ethernet]")
        eth_section.append("mac-address=%s" % match.macaddress.upper())
    elif match is not None and match.name:
        lines.append("interface-name=%s" % match.name)
    else:
        # No match: netplan treats the key as the interface name.
        lines.append("interface-name=%s" % iface_id)
    lines.append("")
    if eth_section:
        lines.extend(eth_section)
        lines.append("")
    lines.extend(_ip_section(4, cfg))
    lines.append("")
    lines.extend(_ip_section(6, cfg))
    lines.append("")
    return "\n".join(lines)


def _vlan_lines(iface_id, cfg):
    lines = ["[connection]",
             "id=%s" % iface_id,
             "uuid=%s" % _conn_uuid(iface_id),
             "type=vlan",
             "interface-name=%s" % iface_id,
             "",
             "[vlan]",
             "id=%d" % cfg.id,
             # Reference the parent by its connection UUID, so it resolves
             # whether the parent binds by name or by MAC.
             "parent=%s" % _conn_uuid(cfg.link),
             ""]
    lines.extend(_ip_section(4, cfg))
    lines.append("")
    lines.extend(_ip_section(6, cfg))
    lines.append("")
    return "\n".join(lines)


def render(network):
    """Render a Network config to ``{filename: keyfile-content}``.

    Each file belongs in /etc/NetworkManager/system-connections/ with mode
    0600. Returns an empty dict if network is None.
    """
    if network is None:
        return {}
    files = {}
    for iface_id, cfg in network.ethernets.items():
        files["%s.nmconnection" % iface_id] = _ethernet_lines(iface_id, cfg)
    for iface_id, cfg in network.vlans.items():
        files["%s.nmconnection" % iface_id] = _vlan_lines(iface_id, cfg)
    return files
