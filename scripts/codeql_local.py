#!/usr/bin/env python3
"""Run CodeQL locally with the exact query set CI uses.

The bundle installed here is the same one github/codeql-action@v3 runs in CI,
so `make codeql` reproduces GitHub's findings 1:1 — without a round-trip
through Actions and without any GitHub credentials.

Flow:
    ensure-cli   download + verify + extract the official CodeQL bundle
    analyze      database create → database analyze (suites from codeql-config.yml)
    summary      render a grouped report from the SARIF (no analysis, fast)

Outputs live in .codeql/ (gitignored):
    .codeql/db            CodeQL database
    .codeql/results.sarif raw SARIF (same format GitHub ingests)
    .codeql/codeql-report.md grouped, human/agent-readable work queue
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / ".github" / "codeql" / "codeql-config.yml"
WORK_DIR = REPO_ROOT / ".codeql"
DB_DIR = WORK_DIR / "db"
SARIF_PATH = WORK_DIR / "results.sarif"
REPORT_PATH = WORK_DIR / "codeql-report.md"

BUNDLE_RELEASE_URL = "https://github.com/github/codeql-action/releases/latest/download/"
BUNDLE_ASSETS = {
    ("Linux", "x86_64"): "codeql-bundle-linux64.tar.zst",
    ("Linux", "aarch64"): "codeql-bundle-linux-arm64.tar.zst",
    ("Darwin", "arm64"): "codeql-bundle-osx64.tar.zst",
    ("Darwin", "x86_64"): "codeql-bundle-osx64.tar.zst",
}
DEFAULT_CLI = Path.home() / ".codeql" / "codeql" / "codeql"

# Suite shorthand (what the action accepts) → fully-qualified CLI reference.
SUITES_PACK = "codeql/{lang}-queries:codeql-suites/{lang}-{alias}.qls"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeqlConfig:
    """Parsed .github/codeql/codeql-config.yml — shared by CI and local runs."""

    suites: tuple[str, ...] = ()
    paths_ignore: tuple[str, ...] = ()
    language: str = "python"

    @classmethod
    def load(cls, path: Path, language: str | None) -> CodeqlConfig:
        import yaml  # noqa: PLC0415 — deferred so `ensure-cli` stays yaml-free

        data = yaml.safe_load(path.read_text()) or {}
        suites = tuple(entry["uses"] for entry in data.get("queries", []) if "uses" in entry)
        ignore = tuple(data.get("paths-ignore", []))
        return cls(
            suites=suites,
            paths_ignore=ignore,
            language=language or "python",
        )

    def cli_suite_refs(self) -> list[str]:
        """Translate shorthand aliases into pack references the CLI accepts."""
        refs = []
        for alias in self.suites:
            if "/" in alias or alias.endswith(".qls"):
                refs.append(alias)  # already a pack/suite reference
            else:
                refs.append(SUITES_PACK.format(lang=self.language, alias=alias))
        return refs

    def is_ignored(self, path: str) -> bool:
        return any(path == pat or path.startswith(pat.rstrip("/") + "/") for pat in self.paths_ignore)


# --------------------------------------------------------------------------
# ensure-cli: install the official bundle
# --------------------------------------------------------------------------


def bundle_asset_name() -> str:
    key = (platform.system(), platform.machine())
    asset = BUNDLE_ASSETS.get(key)
    if asset is None:
        sys.exit(f"Unsupported platform for the CodeQL bundle: {key}")
    return asset


def _stream(url: str, dest: Path) -> None:
    label = f"{dest.name}: "
    with urllib.request.urlopen(url) as resp, dest.open("wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stdout.write(f"\r{label}{done >> 20}/{total >> 20} MiB ({pct}%)")
                sys.stdout.flush()
    print()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_cli(cli: Path) -> None:
    if cli.exists() and os.access(cli, os.X_OK):
        print(f"[codeql] CLI already installed: {cli}")
        return

    asset = bundle_asset_name()
    url = BUNDLE_RELEASE_URL + asset
    dest_dir = cli.parent.parent  # ~/.codeql
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = dest_dir / asset

    print(f"[codeql] downloading {url}")
    _stream(url, archive)

    checksum_url = BUNDLE_RELEASE_URL + asset + ".checksum.txt"
    checksum_path = archive.with_suffix(archive.suffix + ".checksum.txt")
    _stream(checksum_url, checksum_path)
    expected = checksum_path.read_text().split()[0]
    actual = _sha256(archive)
    if actual != expected:
        sys.exit(f"[codeql] checksum mismatch:\n  expected {expected}\n  actual   {actual}")
    print(f"[codeql] checksum OK ({expected[:16]}…)")

    print(f"[codeql] extracting into {dest_dir} (this takes a minute)")
    with tarfile.open(archive, "r:zst") as tar:
        tar.extractall(dest_dir, filter="data")
    archive.unlink()
    checksum_path.unlink()

    if not cli.exists():
        sys.exit(f"[codeql] bundle extracted but CLI not found at {cli}")
    print(f"[codeql] installed: {cli}")


# --------------------------------------------------------------------------
# analyze: database + evaluation
# --------------------------------------------------------------------------


def _run(cmd: list[str]) -> None:
    print(f"[codeql] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        sys.exit(f"[codeql] command failed with exit code {result.returncode}")


def create_database(cli: Path, config: CodeqlConfig) -> None:
    WORK_DIR.mkdir(exist_ok=True)
    _run(
        [
            str(cli),
            "database",
            "create",
            str(DB_DIR),
            f"--language={config.language}",
            f"--source-root={REPO_ROOT}",
            "--overwrite",
            "--threads=0",
        ]
    )


def run_analysis(cli: Path, config: CodeqlConfig) -> None:
    suites = config.cli_suite_refs()
    if not suites:
        sys.exit(f"[codeql] no `queries:` entries in {CONFIG_PATH}")
    WORK_DIR.mkdir(exist_ok=True)
    _run(
        [
            str(cli),
            "database",
            "analyze",
            str(DB_DIR),
            *suites,
            "--download",  # fetch query packs the first time
            "--format=sarif-latest",
            f"--output={SARIF_PATH}",
            "--threads=0",
            "--rerun",  # re-evaluate so results always reflect the current source
        ]
    )
    print(f"[codeql] SARIF written to {SARIF_PATH}")


# --------------------------------------------------------------------------
# summary: grouped report
# --------------------------------------------------------------------------

LEVEL_RANK = {"error": 0, "warning": 1, "note": 2, "none": 3}


@dataclass
class Finding:
    rule: str
    level: str
    message: str
    file: str
    line: int


@dataclass
class RuleGroup:
    rule: str
    level: str
    description: str
    help_uri: str
    findings: list[Finding] = field(default_factory=list)


def _rule_metadata(sarif: dict) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for run in sarif.get("runs", []):
        for rule in run.get("tool", {}).get("driver", {}).get("rules", []):
            meta[rule.get("id", "")] = rule
    return meta


def _description(rule: dict) -> str:
    for key in ("fullDescription", "shortDescription"):
        text = rule.get(key, {}).get("text")
        if text:
            return " ".join(text.split())
    return ""


def load_findings(config: CodeqlConfig) -> tuple[list[Finding], dict[str, dict]]:
    if not SARIF_PATH.exists():
        sys.exit(f"[codeql] {SARIF_PATH} not found — run `make codeql` first")
    sarif = json.loads(SARIF_PATH.read_text())
    meta = _rule_metadata(sarif)

    findings: list[Finding] = []
    skipped = 0
    for run in sarif.get("runs", []):
        for res in run.get("results", []):
            loc = (res.get("locations") or [{}])[0].get("physicalLocation", {})
            file = loc.get("artifactLocation", {}).get("uri", "?")
            region = loc.get("region", {})
            # Paths inside the local database dir are extractor-internal
            # (e.g. parse diagnostics copied from gitignored sources) and
            # never appear on CI.
            if file.startswith(f"{WORK_DIR.name}/") or config.is_ignored(Path(file).as_posix()):
                skipped += 1
                continue
            findings.append(
                Finding(
                    rule=res.get("ruleId", "?"),
                    level=res.get("level", "none"),
                    message=" ".join(res.get("message", {}).get("text", "").split()),
                    file=file,
                    line=int(region.get("startLine", 0)),
                )
            )
    if skipped:
        print(f"[codeql] filtered {skipped} findings under paths-ignore")
    return findings, meta


def group_findings(findings: list[Finding], meta: dict[str, dict]) -> list[RuleGroup]:
    groups: dict[str, RuleGroup] = {}
    for f in findings:
        rule = meta.get(f.rule, {})
        if f.rule not in groups:
            groups[f.rule] = RuleGroup(
                rule=f.rule,
                level=f.level,
                description=_description(rule),
                help_uri=rule.get("helpUri", ""),
            )
        groups[f.rule].findings.append(f)
    return sorted(
        groups.values(),
        key=lambda g: (LEVEL_RANK.get(g.level, 9), -len(g.findings), g.rule),
    )


def render_report(groups: list[RuleGroup]) -> str:
    files = {f.file for g in groups for f in g.findings}
    lines = [
        "# CodeQL local report",
        "",
        f"**{sum(len(g.findings) for g in groups)} findings** across **{len(groups)} rules** in **{len(files)} files**",
        "",
    ]
    for g in groups:
        lines.append(f"## `{g.rule}` — {len(g.findings)}× {g.level}")
        if g.description:
            lines.append(f"> {g.description}")
        if g.help_uri:
            lines.append(f"<{g.help_uri}>")
        lines.append("")
        by_file: dict[str, list[Finding]] = defaultdict(list)
        for f in g.findings:
            by_file[f.file].append(f)
        for file in sorted(by_file):
            lines.append(f"### `{file}`")
            for f in sorted(by_file[file], key=lambda x: x.line):
                lines.append(f"- L{f.line}: {f.message}")
            lines.append("")
    return "\n".join(lines)


def summarize(config: CodeqlConfig, strict: bool) -> None:
    findings, meta = load_findings(config)
    groups = group_findings(findings, meta)

    print(f"\n{'=' * 72}")
    print(f"CodeQL: {len(findings)} findings / {len(groups)} rules")
    print(f"{'=' * 72}")
    for g in groups:
        files = {f.file for f in g.findings}
        print(f"{g.level:>7}  {g.rule:<55} {len(g.findings):>3}× in {len(files)} files")
    if not groups:
        print("clean — no findings 🎉")

    REPORT_PATH.write_text(render_report(groups))
    print(f"\nfull grouped report: {REPORT_PATH}")

    if strict and any(g.level == "error" for g in groups):
        sys.exit(1)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def resolve_cli(args: argparse.Namespace) -> Path:
    return Path(args.cli or os.environ.get("CODEQL_CLI") or DEFAULT_CLI).expanduser()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cli",
        help=f"CodeQL CLI path (default: {DEFAULT_CLI}, env: CODEQL_CLI)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"CodeQL config file (default: {CONFIG_PATH})",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Override analysis language (default: python)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure-cli", help="download + install the CodeQL bundle")
    p_analyze = sub.add_parser("analyze", help="database create + analyze + report")
    p_analyze.add_argument("--strict", action="store_true", help="exit non-zero on error-level findings")
    sub.add_parser("summary", help="render report from an existing SARIF only")

    args = parser.parse_args()
    config = CodeqlConfig.load(args.config, args.language)

    if args.command == "ensure-cli":
        ensure_cli(resolve_cli(args))
    elif args.command == "analyze":
        cli = resolve_cli(args)
        ensure_cli(cli)
        create_database(cli, config)
        run_analysis(cli, config)
        summarize(config, strict=args.strict)
    elif args.command == "summary":
        summarize(config, strict=False)


if __name__ == "__main__":
    main()
