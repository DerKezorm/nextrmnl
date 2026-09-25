"""Where nextrmnl may open connections: the boundary to the outside, set by the operator for all accounts.

Three modes. ``private``: only private, loopback, link-local and carrier-grade NAT ranges (100.64/10, the range
Tailscale, NetBird and other VPNs use). ``list``: private plus the operator's list of networks and names. ``all``:
anything. A name is resolved and *every* address it resolves to must be allowed; a name that matches a name
pattern on the list is allowed as such.
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import socket
from dataclasses import dataclass

from .settings_service import TARGETS_ALL, TARGETS_LIST, TARGETS_PRIVATE

PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""
    addresses: tuple[str, ...] = ()


def is_private(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in PRIVATE_NETWORKS)


def parse_list(text: str | list[str]) -> list[str]:
    items = text if isinstance(text, list) else text.split(",")
    return [item.strip() for item in items if item.strip()]


def invalid_entries(entries: list[str]) -> list[str]:
    """Entries that are neither a network, an address nor a name pattern."""
    bad = []
    for entry in entries:
        if "/" in entry:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                bad.append(entry)
            continue
        try:
            ipaddress.ip_address(entry)
            continue
        except ValueError:
            pass
        cleaned = entry.replace("*", "a").replace("?", "a")
        if not all(part and part.replace("-", "a").isalnum() for part in cleaned.strip(".").split(".")):
            bad.append(entry)
    return bad


def _address_listed(address: str, entries: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    for entry in entries:
        try:
            if "/" in entry:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def _name_listed(host: str, entries: list[str]) -> bool:
    name = host.lower().rstrip(".")
    for entry in entries:
        pattern = entry.lower().rstrip(".")
        if "/" in pattern:
            continue
        try:
            ipaddress.ip_address(pattern)
            continue
        except ValueError:
            pass
        if fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def check(host: str, port: int, mode: str, entries: list[str]) -> Verdict:
    host = host.strip()
    if not host:
        return Verdict(False, "empty_host")
    if mode == TARGETS_ALL:
        return Verdict(True)
    if mode == TARGETS_LIST and _name_listed(host, entries):
        return Verdict(True)
    try:
        addresses = await asyncio.get_running_loop().run_in_executor(None, _resolve, host, port)
    except (socket.gaierror, OSError):
        return Verdict(False, "unresolvable")
    if not addresses:
        return Verdict(False, "unresolvable")
    for address in addresses:
        if is_private(address):
            continue
        if mode == TARGETS_LIST and _address_listed(address, entries):
            continue
        return Verdict(False, "outside", tuple(addresses))
    return Verdict(True, "", tuple(addresses))


MODE_NAMES = {
    TARGETS_PRIVATE: "private networks only",
    TARGETS_LIST: "private networks and the list",
    TARGETS_ALL: "all",
}


def describe_mode(mode: str) -> str:
    return MODE_NAMES[mode]
