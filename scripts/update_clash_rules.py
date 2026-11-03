#!/usr/bin/env python3
"""Build Mihomo providers from this repository's existing sing-box sources.

Run after update_rules.py so both clients use the same downloaded source files.
No ASN database or alternate rule supplier is added. Unsupported nonempty match
fields and logical source rules abort publication for manual review.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import update_spotify_clash as spotify


SENTINELS = frozenset({
    "7h15.ru1353t.1s.m4d3.by.5ukk4w.skk.moe",
    "this_ruleset_is_made_by_sukkaw.ruleset.skk.moe",
})
DOMAIN_FIELDS = frozenset({"domain", "domain_suffix", "domain_keyword", "domain_regex"})
RETAINED_SPOTIFY_SRS = spotify.SOURCE_URL.removesuffix(".json") + ".srs"


def nonempty(value: object) -> bool:
    return value not in (None, False, [], {}, "")


def parse_source(raw: bytes, allowed: frozenset[str]) -> list[dict[str, list[str]]]:
    document = json.loads(raw)
    if not isinstance(document, dict) or set(document) != {"version", "rules"}:
        raise ValueError("Source must contain only version and rules")
    if document["version"] != 2:
        raise ValueError(f"Unsupported source version: {document['version']!r}")
    if not isinstance(document["rules"], list) or not document["rules"]:
        raise ValueError("Source must contain a nonempty rules list")
    result: list[dict[str, list[str]]] = []
    for index, rule in enumerate(document["rules"]):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {index} must be an object")
        if rule.get("type") == "logical" or "rules" in rule:
            raise ValueError(f"Rule {index}: logical source rules require manual review")
        extra = {key for key, value in rule.items() if key not in allowed and nonempty(value)}
        if extra:
            raise ValueError(f"Rule {index}: unsupported nonempty fields: {sorted(extra)}")
        parsed: dict[str, list[str]] = {}
        for key, value in rule.items():
            if key not in allowed:
                continue
            if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"Rule {index}: {key} must be a list of nonempty strings")
            if value:
                parsed[key] = value
        if not parsed:
            raise ValueError(f"Rule {index}: no supported match fields")
        result.append(parsed)
    return result


def simple_parameter(value: str) -> str:
    if value != value.strip() or any(char in value for char in ",\n\r"):
        raise ValueError(f"Match value cannot be represented safely in a text provider: {value!r}")
    return value


def domain_lines(rule: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    for field, rule_type in (("domain", "DOMAIN"), ("domain_suffix", "DOMAIN-SUFFIX"),
                             ("domain_keyword", "DOMAIN-KEYWORD"), ("domain_regex", "DOMAIN-REGEX")):
        for value in rule.get(field, []):
            if field in {"domain", "domain_suffix"}:
                value = spotify.normalize_domain(value, suffix=False)
            else:
                value = simple_parameter(value)
            result.append(f"{rule_type},{value}")
    return result


def network_lines(values: list[str]) -> list[str]:
    networks = {ipaddress.ip_network(value, strict=False) for value in values}
    return [str(network) for network in sorted(
        networks, key=lambda item: (item.version, int(item.network_address), item.prefixlen)
    )]


def combine(operator: str, rules: list[str]) -> str:
    if not rules:
        raise ValueError("Cannot combine an empty match group")
    if len(rules) == 1:
        return rules[0]
    return operator + ",(" + ",".join("(" + rule + ")" for rule in rules) + ")"


def compile_domainset(rules: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"domain": [], "extra": [], "ip": []}
    for rule in rules:
        # The source family intentionally publishes independent domain and IP
        # match material; keep IP material in the later IP routing phase.
        result["domain"].extend(spotify.normalize_domain(value, suffix=False) for value in rule.get("domain", []))
        result["domain"].extend(spotify.normalize_domain(value, suffix=True) for value in rule.get("domain_suffix", []))
        for field, rule_type in (("domain_keyword", "DOMAIN-KEYWORD"), ("domain_regex", "DOMAIN-REGEX")):
            result["extra"].extend(f"{rule_type},{simple_parameter(value)}" for value in rule.get(field, []))
        result["ip"].extend(network_lines(rule.get("ip_cidr", [])))
    return {kind: sorted(set(lines)) for kind, lines in result.items()}


def compile_non_ip(rules: list[dict[str, list[str]]]) -> list[str]:
    result: list[str] = []
    for rule in rules:
        domains = domain_lines(rule)
        processes = ["PROCESS-NAME," + simple_parameter(value) for value in rule.get("process_name", [])]
        if domains and processes:
            # E.g. Bili international: (any domain) AND (any process name).
            # Flattening both categories into separate provider lines would OR
            # them and silently change the sing-box source's process condition.
            result.append(combine("AND", [combine("OR", domains), combine("OR", processes)]))
        else:
            result.extend(domains or processes)
    return sorted(set(result))


def compile_ip(rules: list[dict[str, list[str]]]) -> tuple[list[str], int]:
    result: list[str] = []
    ignored = 0
    for rule in rules:
        for field in ("domain", "domain_suffix"):
            for value in rule.get(field, []):
                if value not in SENTINELS:
                    raise ValueError(f"IP source gained a real {field} match: {value!r}")
                ignored += 1
        result.extend(network_lines(rule.get("ip_cidr", [])))
    return network_lines(result), ignored


def provider_text(lines: list[str], behavior: str, source_url: str, digest: str) -> bytes:
    text = (
        "# Converted from the same sing-box JSON used for the SRS publication.\n"
        f"# Source: {source_url}\n"
        f"# Source SHA-256: {digest}\n"
        f"# Mihomo behavior: {behavior}; format: text\n"
        "# Rule data retains upstream provenance; see SOURCE_LICENSES.md.\n"
    )
    text += "\n".join(lines) + "\n" if lines else "# This source currently has no entries for this provider component.\n"
    return text.encode("utf-8")


def build_outputs(source_root: Path, spotify_source: bytes) -> tuple[dict[str, bytes], dict[str, object]]:
    manifest = json.loads((source_root / "manifest.json").read_bytes())
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("rules"), list):
        raise ValueError("Unsupported source manifest")
    retained = manifest.get("retained_external_rule", {})
    if retained.get("tag") != "rs-Spotify" or retained.get("url") != RETAINED_SPOTIFY_SRS:
        raise ValueError("Retained Spotify upstream changed; review the converter before publication")
    outputs: dict[str, bytes] = {}
    mapping: dict[str, object] = {"schema_version": 1, "source_manifest": "manifest.json", "providers": {}}
    entries = mapping["providers"]
    assert isinstance(entries, dict)
    tags: set[str] = set()
    for item in manifest["rules"]:
        tag, source_path, url = item["tag"], item["source_path"], item["url"]
        if tag in tags or not re.fullmatch(r"[a-z0-9-]+", tag):
            raise ValueError(f"Duplicate or unsafe rule tag: {tag!r}")
        tags.add(tag)
        relative = Path(source_path)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 3 or relative.parts[0] != "sources":
            raise ValueError(f"Unsafe source path: {source_path!r}")
        family, name = relative.parts[1], relative.stem
        if family not in {"domainset", "non_ip", "ip"} or not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError(f"Unsupported source family or filename: {source_path!r}")
        expected_url = f"https://ruleset.skk.moe/sing-box/{family}/{name}.json"
        if url != expected_url:
            raise ValueError(f"Source URL/path mismatch for {tag}")
        raw = (source_root / relative).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        allowed = DOMAIN_FIELDS | {"ip_cidr"} if family == "domainset" else (
            DOMAIN_FIELDS | {"process_name"} if family == "non_ip" else frozenset({"domain", "domain_suffix", "ip_cidr"})
        )
        rules = parse_source(raw, frozenset(allowed))
        source_info: dict[str, object] = {"source": url, "source_path": source_path, "source_sha256": digest, "components": {}}
        components = source_info["components"]
        assert isinstance(components, dict)
        if family == "domainset":
            compiled = compile_domainset(rules)
            for kind, lines in compiled.items():
                behavior = {"domain": "domain", "extra": "classical", "ip": "ipcidr"}[kind]
                path = f"clash/domainset/{name}-{kind}.txt"
                outputs[path] = provider_text(lines, behavior, url, digest)
                components[kind] = {"path": path, "behavior": behavior, "format": "text", "entries": len(lines)}
        else:
            if family == "non_ip":
                lines, behavior = compile_non_ip(rules), "classical"
            else:
                lines, ignored = compile_ip(rules)
                behavior = "ipcidr"
                source_info["ignored_provenance_sentinel_entries"] = ignored
            path = f"clash/{family}/{name}.txt"
            outputs[path] = provider_text(lines, behavior, url, digest)
            components["main"] = {"path": path, "behavior": behavior, "format": "text", "entries": len(lines)}
        entries[tag] = source_info
    spotify_outputs, spotify_stats = spotify.convert_source(spotify_source)
    for filename, content in spotify_outputs.items():
        outputs[f"clash/{filename}"] = content
    entries["rs-Spotify"] = {
        "source": spotify.SOURCE_URL, "source_sha256": spotify_stats["source_sha256"],
        "components": {
            "domain": {"path": "clash/spotify-domain.txt", "behavior": "domain", "format": "text", "entries": spotify_stats["domain_entries"]},
            "ip": {"path": "clash/spotify-ip.txt", "behavior": "ipcidr", "format": "text", "entries": spotify_stats["ip_entries"]},
        },
    }
    outputs["clash/mapping.json"] = (json.dumps(mapping, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    return outputs, mapping


def write_outputs(root: Path, outputs: dict[str, bytes]) -> None:
    """Prepare every output first, then replace; roll back caught write failures."""
    root.mkdir(parents=True, exist_ok=True)
    staged: dict[Path, Path] = {}
    original: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for relative, content in outputs.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            original[target] = target.read_bytes() if target.exists() else None
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".clash-", delete=False) as stream:
                staged[target] = Path(stream.name)
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
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-root", type=Path, help="Defaults to the source repository root")
    parser.add_argument("--spotify-source-file", type=Path, help="Use a downloaded public Spotify source for offline validation")
    arguments = parser.parse_args()
    spotify_source = arguments.spotify_source_file.read_bytes() if arguments.spotify_source_file else spotify.fetch_source()
    outputs, mapping = build_outputs(arguments.source_root, spotify_source)
    write_outputs(arguments.output_root or arguments.source_root, outputs)
    print(json.dumps({"rule_sets": len(mapping["providers"]), "output_files": len(outputs)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Clash conversion failed: {error}", file=sys.stderr)
        raise SystemExit(1)
