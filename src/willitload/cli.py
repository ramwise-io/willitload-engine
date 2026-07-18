"""
willitload.cli -- CLI entry point and rich text renderer.

Two subcommands:
  willitload scan <path-expr> [--json] [--no-color]
  willitload check <path-expr> --against <baseline> [--align name|position]
                   [--extra strict|open] [--json] [--no-color]

Renderer discipline (non-negotiable):
  The rich renderer formats ONLY what is already in ScanResult/CheckResult.
  It never groups, sorts, summarizes, or derives values.
  If a value is needed for display, it lives in the model first.

Exit code contract:
  0 = clean (no ERROR findings)
  1 = broken files found (any ERROR-severity finding)
  2 = tool error (invalid args, unreadable baseline, etc.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import argparse

from willitload import __version__
from willitload.models import (
    AlignmentMode,
    ExtraColumnPolicy,
    Severity,
    CheckResult,
    ScanResult,
    FileVerdict,
    Verdict,
)

# Detect NO_COLOR env variable (https://no-color.org/)
_NO_COLOR = "NO_COLOR" in os.environ


def _use_color(no_color_flag: bool) -> bool:
    return not (no_color_flag or _NO_COLOR)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="willitload",
        description=(
            "Fileset structural pre-flight for bulk loads. "
            "Finds the structurally broken files before your loader does."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"willitload {__version__}")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- scan ---
    scan_p = subparsers.add_parser(
        "scan",
        help="Describe a fileset's structure: families, variants, outliers.",
    )
    scan_p.add_argument("path", help="Directory, glob, or file path to scan.")
    scan_p.add_argument("--json", action="store_true", help="Emit JSON (the API contract).")
    scan_p.add_argument(
        "--file-ceiling",
        type=int,
        default=10_000,
        metavar="N",
        help="Maximum files to scan (default: 10,000).",
    )

    # --- check ---
    check_p = subparsers.add_parser(
        "check",
        help="Check fileset against a baseline; produce golden/broken partition.",
    )
    check_p.add_argument("path", help="Directory, glob, or file path to check.")
    check_p.add_argument(
        "--against",
        required=True,
        metavar="BASELINE",
        help=(
            "Baseline source: path to a flat schema file (.schema/.txt), "
            "a prior scan JSON, or a golden sample file."
        ),
    )
    check_p.add_argument(
        "--align",
        choices=["name", "position"],
        default="name",
        help="Alignment mode (default: name).",
    )
    check_p.add_argument(
        "--extra",
        choices=["strict", "open"],
        default="strict",
        help="Extra-column policy (default: strict).",
    )
    check_p.add_argument("--json", action="store_true", help="Emit JSON (the API contract).")
    check_p.add_argument(
        "--file-ceiling",
        type=int,
        default=10_000,
        metavar="N",
        help="Maximum files to scan (default: 10,000).",
    )

    return parser


# ---------------------------------------------------------------------------
# Baseline auto-detection
# ---------------------------------------------------------------------------

def _load_baseline(path_str: str):
    """Auto-detect and load a baseline from any of the three front-doors."""
    from willitload.baseline.flat import parse_flat_schema
    from willitload.baseline.from_json import parse_from_scan_json
    from willitload.baseline.golden import parse_golden_file

    path = Path(path_str)
    if not path.exists():
        print(f"Error: baseline path not found: {path_str}", file=sys.stderr)
        sys.exit(2)

    suffix = path.suffix.lower()

    # JSON -> try prior-scan round-trip first
    if suffix == ".json":
        try:
            return parse_from_scan_json(path)
        except ValueError:
            pass  # fall through to golden-file

    # SQL/DDL -> try DDL parsing
    if suffix in (".sql", ".ddl"):
        from willitload.baseline.ddl import parse_ddl_schema
        try:
            return parse_ddl_schema(path)
        except ValueError:
            pass  # fall through to golden-file

    # Peek inside file to see if it is DDL (starts with CREATE)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            peek = f.read(200).strip().upper()
        if peek.startswith("CREATE"):
            from willitload.baseline.ddl import parse_ddl_schema
            return parse_ddl_schema(path)
    except Exception:
        pass

    # Known schema file extensions -> flat schema
    if suffix in (".schema", ".txt", ""):
        try:
            return parse_flat_schema(path)
        except ValueError:
            pass  # fall through to golden-file

    # Last resort: try as a golden sample file
    try:
        return parse_golden_file(path)
    except ValueError as e:
        print(f"Error: could not load baseline from {path_str}: {e}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Rich text renderer
# ---------------------------------------------------------------------------

def _render_scan(result: ScanResult, use_color: bool) -> None:
    """Render a ScanResult as human-readable text. Formats only; never derives."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        from rich.text import Text

        console = Console(highlight=False, no_color=not use_color)
    except ImportError:
        _render_scan_plain(result)
        return

    acc = result.accounting

    # Header
    console.print(f"\n[bold]willitload scan[/bold] -- {result.path_expression}")
    console.print(
        f"  [dim]{acc.files_seen} files seen  |  "
        f"{acc.profiled} profiled  |  {acc.degraded} degraded  |  "
        f"{acc.catalogued} catalogued  |  {acc.refused} refused[/dim]"
    )
    console.print(f"  [dim]Elapsed: {result.elapsed_ms:.0f}ms[/dim]\n")

    # Set-level findings
    if result.scan_findings:
        console.print("[bold yellow]Fileset Findings[/bold yellow]")
        for f in result.scan_findings:
            console.print(f"  [{_severity_color(f.severity)}]{f.severity.value}[/] {f.explanation}")
        console.print()

    # Families
    if result.families:
        console.print(f"[bold]{len(result.families)} structural {'family' if len(result.families)==1 else 'families'} detected[/bold]")
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
        table.add_column("Family", style="cyan")
        table.add_column("Files", justify="right")
        table.add_column("Columns", justify="right")
        table.add_column("Type Variants", justify="right")
        table.add_column("Representative Columns", no_wrap=False, max_width=60)

        for fam in result.families:
            cols_preview = ", ".join(fam.representative_columns[:6])
            if len(fam.representative_columns) > 6:
                cols_preview += f"... (+{len(fam.representative_columns)-6})"
            table.add_row(
                fam.family_id,
                str(fam.file_count),
                str(fam.column_count),
                str(fam.type_variants),
                cols_preview,
            )
        console.print(table)

    # Broken files
    broken = [v for v in result.file_verdicts if v.verdict == Verdict.BROKEN]
    if broken:
        console.print(f"\n[bold red]{len(broken)} broken file(s)[/bold red]")
        for v in broken:
            console.print(f"  [red][X][/red] {v.path}")
            for f in v.findings:
                console.print(
                    f"      [{_severity_color(f.severity)}]{f.severity.value}[/] "
                    f"{f.locus}: {f.explanation}"
                )
    else:
        console.print("[green]No structural anomalies detected within the fileset.[/green]")

    console.print()


def _render_check(result: CheckResult, use_color: bool) -> None:
    """Render a CheckResult as human-readable text. Formats only; never derives."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box

        console = Console(highlight=False, no_color=not use_color)
    except ImportError:
        _render_check_plain(result)
        return

    acc = result.accounting

    # Header
    console.print(f"\n[bold]willitload check[/bold] -- {result.path_expression}")
    console.print(f"  Baseline: {result.baseline_source}")
    console.print(f"  Mode: [bold]{result.alignment_mode.value}[/bold]  |  Extra-column policy: {result.extra_column_policy.value}")
    console.print(
        f"  [dim]{acc.files_seen} files seen  |  "
        f"{len(result.golden)} golden  |  {len(result.warned)} warned  |  "
        f"{len(result.broken)} broken[/dim]"
    )
    console.print(f"  [dim]Elapsed: {result.elapsed_ms:.0f}ms[/dim]\n")

    # Set-level findings
    if result.scan_findings:
        console.print("[bold yellow]Fileset Findings[/bold yellow]")
        for f in result.scan_findings:
            console.print(f"  [{_severity_color(f.severity)}]{f.severity.value}[/] {f.explanation}")
        console.print()

    # Broken files
    if result.broken:
        console.print(f"[bold red]BROKEN - {len(result.broken)} file(s) do not conform[/bold red]")
        for v in result.broken:
            console.print(f"\n  [red][X][/red] {v.path}")
            for f in v.findings:
                console.print(
                    f"      [{_severity_color(f.severity)}]{f.severity.value}[/] "
                    f"{f.locus}: {f.explanation}"
                )
    else:
        console.print("[bold green]All files conform to the baseline.[/bold green]")

    # Warned files
    if result.warned:
        console.print(f"\n[bold yellow]{len(result.warned)} file(s) with warnings[/bold yellow]")
        for v in result.warned:
            console.print(f"  [yellow][!][/yellow] {v.path}")
            for f in v.findings:
                console.print(
                    f"      [{_severity_color(f.severity)}]{f.severity.value}[/] "
                    f"{f.locus}: {f.explanation}"
                )

    # Golden summary
    if result.golden:
        console.print(
            f"\n[green][OK] {len(result.golden)} file(s) conform[/green] -- "
            f"[dim]'Conforms' = structurally matches the declared contract. "
            f"Does NOT mean values are correct.[/dim]"
        )

    console.print()


def _severity_color(severity: Severity) -> str:
    match severity:
        case Severity.ERROR:
            return "bold red"
        case Severity.WARN:
            return "yellow"
        case Severity.INFO:
            return "dim"
        case _:
            return "white"


def _render_scan_plain(result: ScanResult) -> None:
    """Plain-text fallback (no rich)."""
    acc = result.accounting
    print(f"\nwillitload scan -- {result.path_expression}")
    print(f"  {acc.files_seen} files seen | {acc.profiled} profiled | {acc.refused} refused")
    print(f"  {len(result.families)} families | elapsed: {result.elapsed_ms:.0f}ms")
    broken = [v for v in result.file_verdicts if v.verdict == Verdict.BROKEN]
    if broken:
        print(f"\n{len(broken)} broken file(s):")
        for v in broken:
            print(f"  BROKEN  {v.path}")
            for f in v.findings:
                print(f"    {f.severity.value}  {f.locus}: {f.explanation}")
    else:
        print("No anomalies detected.")


def _render_check_plain(result: CheckResult) -> None:
    """Plain-text fallback (no rich)."""
    acc = result.accounting
    print(f"\nwillitload check -- {result.path_expression}")
    print(f"  Baseline: {result.baseline_source}  Mode: {result.alignment_mode.value}")
    print(f"  {acc.files_seen} seen | {len(result.golden)} golden | {len(result.warned)} warned | {len(result.broken)} broken")
    if result.broken:
        print(f"\nBROKEN -- {len(result.broken)} file(s):")
        for v in result.broken:
            print(f"  {v.path}")
            for f in v.findings:
                print(f"    {f.severity.value}  {f.locus}: {f.explanation}")
    else:
        print("All files conform.")
    if result.golden:
        print(f"\n{len(result.golden)} file(s) conform (structural match only).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    use_color = _use_color(args.no_color)

    from willitload.tier0.resolver import ResolverConfig

    resolver_config = ResolverConfig(
        file_count_ceiling=getattr(args, "file_ceiling", 10_000)
    )

    if args.command == "scan":
        from willitload.core import scan
        result = scan(args.path, resolver_config)

        if args.json:
            print(result.to_json())
        else:
            _render_scan(result, use_color)

        # Exit code: 0 even if broken files found (scan is informational)
        sys.exit(0)

    elif args.command == "check":
        baseline = _load_baseline(args.against)

        mode = AlignmentMode(args.align)
        extra_policy = ExtraColumnPolicy(args.extra)

        from willitload.core import check
        result = check(args.path, baseline, mode, extra_policy, resolver_config)

        if args.json:
            print(result.to_json())
        else:
            _render_check(result, use_color)

        # Exit 1 if any broken files (ERROR-severity findings)
        sys.exit(1 if result.has_errors else 0)


if __name__ == "__main__":
    main()
