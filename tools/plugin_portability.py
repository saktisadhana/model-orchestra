"""Audit Claude Code plugin workflows ported to Zed Agent Skills."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

PARITY = {
    "ponytail": ("direct", "Instruction-only workflow maps directly to an Agent Skill."),
    "caveman": ("direct", "Output-style instructions map directly to an Agent Skill."),
    "agent-sdk-dev": ("direct", "Development guidance is available as an Agent Skill."),
    "claude-opus-4-5-migration": ("direct", "Migration guidance is available by explicit skill invocation."),
    "commit-commands": ("partial", "Commit workflow is available, but Claude slash commands are not installed in Zed."),
    "code-review": ("direct", "Review instructions and local tools are available in Zed."),
    "explanatory-output-style": ("direct", "Output-style instructions map directly to an Agent Skill."),
    "feature-dev": ("direct", "The discovery, implementation, and review workflow maps to Zed tools."),
    "frontend-design": ("direct", "Design and implementation guidance is available as an Agent Skill."),
    "hookify": ("partial", "It can create Zed instructions and checks, but Claude lifecycle hooks do not run in Zed."),
    "learning-output-style": ("direct", "Learning-oriented response guidance maps directly to an Agent Skill."),
    "plugin-dev": ("partial", "Zed can maintain Claude plugins, but it cannot execute their manifests as Zed plugins."),
    "pr-review-toolkit": ("direct", "The multi-aspect review workflow maps to Zed tools and delegation."),
    "ralph-wiggum": ("partial", "A bounded loop can be requested, but Claude Stop-hook reinjection is unavailable."),
    "security-guidance": ("partial", "Explicit security review works; automatic edit and stop hooks are unavailable."),
    "claude-mem": ("partial", "Memory can use MCP or files, but automatic session injection is not configured."),
    "obsidian": ("direct", "Native Obsidian Agent Skills are installed alongside the routing skill."),
    "ecc": ("partial", "ECC skills are installed, but Claude hooks, commands, and agent manifests are not Zed runtimes."),
}


def parse_args() -> argparse.Namespace:
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--claude-settings",
        type=Path,
        default=home / ".claude" / "settings.json",
        help="Path to Claude Code settings.json",
    )
    parser.add_argument(
        "--skill-root",
        type=Path,
        default=home / ".agents" / "skills",
        help="Path to the Zed Agent Skills directory",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def enabled_plugins(settings_path: Path) -> list[str]:
    with settings_path.open(encoding="utf-8") as handle:
        settings = json.load(handle)
    configured = settings.get("enabledPlugins", {})
    if not isinstance(configured, dict):
        raise ValueError("enabledPlugins must be an object")
    return sorted(plugin_id for plugin_id, enabled in configured.items() if enabled is True)


def frontmatter_fields(path: Path) -> tuple[dict[str, str], list[str]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    errors: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, ["missing opening frontmatter delimiter"]

    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}, ["missing closing frontmatter delimiter"]

    fields: dict[str, str] = {}
    frontmatter = lines[1:closing]
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        match = re.match(r"^([a-zA-Z0-9-]+):(?:\s*(.*))?$", line)
        if not match:
            index += 1
            continue
        key, value = match.group(1), (match.group(2) or "").strip()
        if value in {">", ">-", "|", "|-"}:
            folded: list[str] = []
            index += 1
            while index < len(frontmatter) and (
                not frontmatter[index] or frontmatter[index][0].isspace()
            ):
                folded.append(frontmatter[index].strip())
                index += 1
            fields[key] = " ".join(part for part in folded if part)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        elif ": " in value:
            errors.append(f"unquoted mapping separator in {key}")
        fields[key] = value
        index += 1
    return fields, errors


def validate_skills(skill_root: Path) -> tuple[list[str], list[str]]:
    names: list[str] = []
    errors: list[str] = []
    for path in sorted(skill_root.glob("*/SKILL.md")):
        directory_name = path.parent.name
        names.append(directory_name)
        fields, field_errors = frontmatter_fields(path)
        errors.extend(f"{directory_name}: {error}" for error in field_errors)
        name = fields.get("name")
        description = fields.get("description", "")
        if name != directory_name:
            errors.append(f"{directory_name}: frontmatter name is {name!r}")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            errors.append(f"{directory_name}: invalid skill name")
        if not 1 <= len(description) <= 1024:
            errors.append(f"{directory_name}: description must be 1-1024 characters")
    return names, errors


def build_report(settings_path: Path, skill_root: Path) -> dict[str, object]:
    plugin_ids = enabled_plugins(settings_path)
    skill_names, skill_errors = validate_skills(skill_root)
    installed = set(skill_names)
    plugins = []
    for plugin_id in plugin_ids:
        name = plugin_id.split("@", 1)[0]
        status, note = PARITY.get(name, ("unclassified", "No portability rule is defined."))
        plugins.append(
            {
                "plugin": plugin_id,
                "skill": name,
                "installed": name in installed,
                "parity": status,
                "note": note,
            }
        )

    counts = {status: sum(item["parity"] == status for item in plugins) for status in (
        "direct", "partial", "unavailable", "unclassified"
    )}
    return {
        "claude_settings": str(settings_path),
        "skill_root": str(skill_root),
        "enabled_plugins": len(plugin_ids),
        "installed_plugin_skills": sum(bool(item["installed"]) for item in plugins),
        "total_zed_skills": len(skill_names),
        "parity_counts": counts,
        "skill_validation_errors": skill_errors,
        "plugins": plugins,
    }


def print_report(report: dict[str, object]) -> None:
    print(f"Enabled Claude plugins: {report['enabled_plugins']}")
    print(
        "Installed Zed plugin skills: "
        f"{report['installed_plugin_skills']}/{report['enabled_plugins']}"
    )
    print(f"Total Zed skills: {report['total_zed_skills']}")
    counts = report["parity_counts"]
    assert isinstance(counts, dict)
    print(
        "Parity: "
        f"{counts['direct']} direct, {counts['partial']} partial, "
        f"{counts['unavailable']} unavailable, {counts['unclassified']} unclassified"
    )
    errors = report["skill_validation_errors"]
    assert isinstance(errors, list)
    print(f"Skill validation errors: {len(errors)}")
    print()
    plugins = report["plugins"]
    assert isinstance(plugins, list)
    for item in plugins:
        assert isinstance(item, dict)
        marker = "installed" if item["installed"] else "MISSING"
        print(f"- {item['plugin']}: {item['parity']} ({marker})")
        print(f"  {item['note']}")
    if errors:
        print("\nValidation errors:")
        for error in errors:
            print(f"- {error}")


def main() -> int:
    args = parse_args()
    try:
        report = build_report(args.claude_settings, args.skill_root)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"plugin portability audit failed: {error}")
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    plugins = report["plugins"]
    assert isinstance(plugins, list)
    missing = any(not item["installed"] for item in plugins)
    unclassified = any(item["parity"] == "unclassified" for item in plugins)
    return int(missing or unclassified or bool(report["skill_validation_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
