# `willitload` Version Contract & Stability Guarantees

This document outlines the stability commitments and versioning discipline for the `willitload` engine. Since the engine is built to be run as an automated ingestion gate in CI/CD pipelines and orchestration DAGs (e.g., Airflow, Prefect, GitHub Actions), maintaining stable integration boundaries is a core priority.

---

## 🛡️ Stable Public Interfaces (The Contract)

Users and pipeline developers are encouraged to rely on the following public boundaries. Any breaking changes to these interfaces will respect the [Versioning Rules](#-versioning-rules) outlined below.

### 1. CLI Commands & Flags
* **Command Names:** `scan` and `check`.
* **Flag Names & Syntax:**
  * `--against` (target schema or baseline file)
  * `--align` (`name` or `position`)
  * `--extra` (`strict` or `ignore`)
  * `--json` (machine-readable JSON format output)
  * `--version` and `--help`

### 2. Exit Code Semantics
Orchestration systems and CI gates rely on exit codes to determine whether to proceed with downstream loading:
* **`0`**: The fileset successfully conforms to the schema/rules (warnings may be present, but no blocking errors).
* **`1`**: The fileset is broken (at least one file contains structural anomalies, invalid formats, type mismatches, or schema drift under a strict policy).
* **Other non-zero values**: System errors, usage mistakes, or missing files.

### 3. JSON Output Schema Shape
When executing `willitload` commands with the `--json` flag, the structure of the JSON payload is guaranteed to remain stable. This includes key structures like:
* Top-level keys: `path_expression`, `baseline_source`, `alignment_mode`, `extra_column_policy`, `has_errors`, `elapsed_ms`, `accounting`.
* Nested arrays: `golden`, `warned`, `broken`, `families`, `scan_findings`.
* Field mappings inside findings: `reason_code`, `severity`, `locus`, `expected`, `found`, `explanation`, `confidence`.

---

## ⚙️ Unstable & Internal Interfaces

* **Internal Python APIs:** While Python users can import functions from `willitload.core`, the underlying Python module structure, class structures, and function signatures are subject to internal refactoring without deprecation warning paths. Standard usage should rely on the CLI interface.

---

## 📈 Versioning Rules

Prior to the `1.0.0` release, we follow a modified Semantic Versioning (SemVer) discipline to balance agility with stability:

1. **Patch Releases (`0.x.y` to `0.x.y+1`):**
   * Reserved strictly for bug fixes, performance improvements, and documentation.
   * **Guarantee:** Patch releases will *never* break the CLI flag parameters, exit-code semantics, or JSON schema shape.
2. **Minor Releases (`0.x.0` to `0.x+1.0`):**
   * May introduce new features, new CLI commands, or backward-incompatible changes (such as modifications to the JSON output shape or CLI flags).
   * Any breaking changes in a minor release will be accompanied by migration instructions in the release notes.
3. **Major Release (`1.0.0`):**
   * Bumping to `1.0.0` will freeze the public contract and transition the project to standard Semantic Versioning.

---

## 🐍 Pure-Python Universal Wheel Guarantee

To ensure friction-free distribution across all environments (local machines, serverless runtimes, containerized CI runners), we commit to keeping the `willitload` package pure Python:
* **Platform Portability:** `willitload` will build and publish as a single `py3-none-any` wheel that installs instantly on any OS/architecture.
* **No Native Extensions:** We will not introduce compiled native extensions (C, C++, Rust) that require pre-built binaries or complicate installation on specialized platform matrices.
* **Database Driver Boundary:** All SQL/parsing engine operations are offloaded onto the pure-Python interfaces of standard drivers (e.g. `duckdb`), keeping our package lightweight and portable.
