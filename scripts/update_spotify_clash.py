#!/usr/bin/env python3
"""Convert the retained Yuu518 Spotify source to native Mihomo text providers.

Only public rule data is read or written. Unknown match fields fail closed rather
than silently broadening or dropping rules. All outputs are prepared and
validated before replacing any existing file; a failed download/conversion leaves
the previous published providers intact. GitHub Actions should commit only after
this command exits successfully.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.request import Request, urlopen


SOURCE_URL = (
    "https://raw.githubusercontent.com/Yuu518/sing-box-rules/"
    "rule_set/rule_set_site/spotify.json"
)
SOURCE_REPOSITORY = "https://github.com/Yuu518/sing-box-rules"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
ALLOWED_FIELDS = frozenset({"domain", "domain_suffix", "ip_cidr"})


def fetch_source() -> bytes:
    error: Exception | None = None
    for attempt in range(3):
        try:
            request = Request(SOURCE_URL, headers={"User-Agent": "spotify-mihomo-converter/1.0"})
            with urlopen(request, timeout=30) as response:
                body = response.read(MAX_SOURCE_BYTES + 1)
            if len(body) > MAX_SOURCE_BYTES:
                raise ValueError("Spotify source exceeds the 2 MiB safety limit")
            return body
        except (OSError, ValueError) as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Cannot download retained Spotify source: {error}")


def normalize_domain(value: object, *, suffix: bool) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Domain values must be nonempty strings without surrounding whitespace")
    if suffix and value.startswith("."):
        raise ValueError("A leading-dot domain suffix requires manual semantic review")
    value = value.rstrip(".").lower()
    if not value or any(char.isspace() or char in "*,/#:+\\" for char in value):
        raise ValueError(f"Unsupported domain syntax: {value!r}")
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"Invalid domain: {value!r}") from exc
    if len(value) > 253 or any(not label or len(label) > 63 for label in value.split(".")):
        raise ValueError(f"Invalid domain length: {value!r}")
    if any(not (char.isalnum() or char in ".-_") for char in value):
        raise ValueError(f"Invalid domain character: {value!r}")
    return "+." + value if suffix else value


def convert_source(raw: bytes) -> tuple[dict[str, bytes], dict[str, object]]:
    document = json.loads(raw)
    if not isinstance(document, dict) or set(document) != {"version", "rules"}:
        raise ValueError("Expected a source object containing only version and rules")
    if document["version"] != 2:
        raise ValueError(f"Unsupported sing-box rule-set version: {document['version']!r}")
    rules = document["rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("Spotify source must contain at least one rule")

    domains: set[str] = set()
    networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    counts = {"domain": 0, "domain_suffix": 0, "ip_cidr": 0}
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not rule or set(rule) - ALLOWED_FIELDS:
            raise ValueError(f"Rule {index} contains unsupported fields or is empty")
        # Headless rules combine domain variants and ip_cidr in the same OR
        # group. Keep those two parts in their respective routing phases.
        for field, values in rule.items():
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ValueError(f"Rule {index} field {field} must be a list of strings")
            counts[field] += len(values)
            for value in values:
                if field == "ip_cidr":
                    networks.add(ipaddress.ip_network(value, strict=False))
                else:
                    domains.add(normalize_domain(value, suffix=field == "domain_suffix"))
    if not domains:
        raise ValueError("Refusing to replace Spotify domain provider with an empty set")

    digest = hashlib.sha256(raw).hexdigest()
    header = (
        "# Spotify rules converted from the retained Yuu518 sing-box source.\n"
        f"# Source: {SOURCE_URL}\n"
        f"# Upstream repository: {SOURCE_REPOSITORY}\n"
        f"# Source SHA-256: {digest}\n"
        "# Rule data retains upstream provenance; no new license is asserted.\n"
    )
    domain_text = header + "# Mihomo behavior: domain; format: text\n" + "\n".join(sorted(domains)) + "\n"
    sorted_ips = sorted(networks, key=lambda item: (item.version, int(item.network_address), item.prefixlen))
    ip_text = header + "# Mihomo behavior: ipcidr; format: text\n"
    ip_text += "\n".join(str(network) for network in sorted_ips)
    ip_text += "\n" if sorted_ips else "# The upstream source currently contains no IP rules.\n"
    stats: dict[str, object] = {
        "source": SOURCE_URL,
        "source_sha256": digest,
        "input_counts": counts,
        "domain_entries": len(domains),
        "ip_entries": len(networks),
    }
    return {
        "spotify-domain.txt": domain_text.encode("utf-8"),
        "spotify-ip.txt": ip_text.encode("utf-8"),
    }, stats


def write_outputs(directory: Path, outputs: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    staged: dict[Path, Path] = {}
    original: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for filename, content in outputs.items():
            target = directory / filename
            original[target] = target.read_bytes() if target.exists() else None
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".spotify-", delete=False) as stream:
                temporary = Path(stream.name)
                staged[target] = temporary
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for target, temporary in staged.items():
            os.replace(temporary, target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            previous = original[target]
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(previous)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-file", type=Path, help="Use an already downloaded public source for offline validation")
    arguments = parser.parse_args()
    raw = arguments.source_file.read_bytes() if arguments.source_file else fetch_source()
    outputs, stats = convert_source(raw)
    write_outputs(arguments.output_root / "clash", outputs)
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Spotify conversion failed: {error}", file=sys.stderr)
        raise SystemExit(1)
