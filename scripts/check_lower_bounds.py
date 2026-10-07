"""Test the built wheel with the lowest resolvable direct runtime dependencies."""

import argparse
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from check_installation import CERTBOT_CLI, CHECK_METADATA, CHECK_PLUGIN

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9–3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", default=sys.executable, help="Host interpreter or Python version"
    )
    parser.add_argument("--certbot", required=True, choices=["3", "5"], help="Certbot host series")
    args = parser.parse_args()
    wheels = list((ROOT / "dist" / "alias").glob("*.whl"))
    if len(wheels) != 1:
        parser.error("Build distributions first; expected exactly one main wheel")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_requirement = next(
        item for item in config["dependency-groups"]["dev"] if item.startswith("pytest>=")
    )
    with TemporaryDirectory(prefix="certbot-dns-lower-bounds-") as directory:
        cwd = Path(directory)

        def run(*arguments: str) -> None:
            subprocess.run(arguments, check=True, cwd=cwd)

        environment = cwd / "host"
        run("uv", "venv", "--python", args.python, str(environment))
        executable = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        python = str(environment / executable)
        # Keep test dependencies out of runtime metadata and modern dev-group constraints
        # out of legacy hosts. Generate all inputs afresh; never reuse an old resolution.
        test_requirements = cwd / "tests.in"
        test_requirements.write_text(pytest_requirement + "\n", encoding="utf-8")
        host_constraints = cwd / "host.in"
        major = int(args.certbot)
        host_constraints.write_text(
            f"certbot>={major},<{major + 1}\nacme>={major},<{major + 1}\n", encoding="utf-8"
        )
        resolved = cwd / "resolved.txt"
        run(
            "uv",
            "pip",
            "compile",
            str(ROOT / "pyproject.toml"),
            str(test_requirements),
            "--python",
            python,
            "--resolution",
            "lowest-direct",
            "--no-sources",
            "--constraint",
            str(host_constraints),
            "--output-file",
            str(resolved),
            *(["--extra", "certbot3"] if major == 3 else []),
        )
        run("uv", "pip", "sync", "--python", python, str(resolved))
        # Do not let wheel installation upgrade the resolved lower bounds.
        run("uv", "pip", "install", "--python", python, "--no-deps", str(wheels[0]))
        run("uv", "pip", "check", "--python", python)
        run("uv", "pip", "freeze", "--python", python)
        run(
            python,
            "-c",
            CHECK_METADATA
            + CHECK_PLUGIN
            + f"assert metadata.version('certbot').split('.')[0] == {args.certbot!r}\n"
            + f"assert metadata.version('acme').split('.')[0] == {args.certbot!r}\n"
            + f"assert main.version == {config['project']['version']!r}\n",
        )
        run(python, "-m", "pytest", "--import-mode=importlib", str(ROOT / "tests"))
        result = subprocess.run(
            [
                python,
                "-c",
                CHECK_METADATA + CERTBOT_CLI,
                "plugins",
                "--text",
                "--config-dir",
                str(cwd / "config"),
                "--work-dir",
                str(cwd / "work"),
                "--logs-dir",
                str(cwd / "logs"),
            ],
            check=True,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        print(result.stdout)
        assert "dns-alias" in result.stdout, result.stderr


if __name__ == "__main__":
    main()
