"""Report audit findings without treating accepted Python 3.9 findings as a tool error."""

import argparse
import json
from pathlib import Path

from certbot.compat import os


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--exit-code", required=True, type=int)
    arguments = parser.parse_args()
    try:
        dependencies = json.loads(arguments.report.read_text(encoding="utf-8"))["dependencies"]
        if not isinstance(dependencies, list):
            raise ValueError("Invalid dependency list")
        rows = []
        affected = 0
        for dependency in dependencies:
            if "skip_reason" in dependency or not isinstance(dependency["vulns"], list):
                raise ValueError("Incomplete audit")
            if dependency["vulns"]:
                affected += 1
            for vulnerability in dependency["vulns"]:
                rows.append(
                    f"| {dependency['name']} | {dependency['version']} | "
                    f"{vulnerability['id']} | "
                    f"{', '.join(vulnerability['fix_versions']) or 'None published'} |"
                )
        if arguments.exit_code not in {0, 1} or bool(rows) != (arguments.exit_code == 1):
            raise ValueError("Audit exit code does not match the report")
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "::error::Dependency audit failed or produced a missing, invalid, or incomplete report."
        )
        return 1

    summary = (
        f"Dependency audit (Python {arguments.python_version}): "
        f"{len(rows)} vulnerability records in {affected} packages."
    )
    report = summary + "\n"
    if rows:
        report += "\n| Package | Version | Advisory | Fix versions |\n| --- | --- | --- | --- |\n"
        report += "\n".join(rows) + "\n"
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as output:
            output.write(report + "\n")
    if rows and arguments.python_version == "3.9":
        print(
            "::warning::Python 3.9 dependency findings are non-blocking; review the audit report."
        )
        return 0
    if rows:
        print("::error::Dependency vulnerabilities block CI on this Python version.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
