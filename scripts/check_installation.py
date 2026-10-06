"""Verify alias installation and removal against an isolated Certbot host."""

import argparse
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
CHECK_PLUGIN = """
from importlib.metadata import distribution, entry_points
from certbot.plugins.dns_common import DNSAuthenticator

main = distribution('certbot-dns-alias')
plugins = [p for p in entry_points(group='certbot.plugins') if p.name == 'dns-alias']
assert len(plugins) == 1, plugins
assert issubclass(plugins[0].load(), DNSAuthenticator)
print('Host loads dns-alias', main.version)
"""
CHECK_ALIAS = """
from importlib.metadata import distribution

alias = distribution('certbot-dns-delegation')
main = distribution('certbot-dns-alias')
assert alias.version == main.version
assert alias.requires == [f'certbot-dns-alias=={main.version}'], alias.requires
assert not alias.entry_points
assert all('.dist-info/' in str(path) for path in alias.files), alias.files
"""
CHECK_REMOVED = """
from importlib.metadata import PackageNotFoundError, distribution

try:
    distribution('certbot-dns-delegation')
except PackageNotFoundError:
    pass
else:
    raise AssertionError('Alias was not removed')
"""
CERTBOT_CLI = """
import sys
from importlib.metadata import distribution

cli = next(p for p in distribution('certbot').entry_points
           if p.group == 'console_scripts' and p.name == 'certbot')
sys.exit(cli.load()())
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", required=True, type=Path, help="Isolated host Python interpreter"
    )
    # Keep the venv interpreter's symlink path so Python retains its host environment.
    python = parser.parse_args().python.absolute()
    with TemporaryDirectory(prefix="certbot-dns-install-check-") as directory:
        cwd = Path(directory)

        def run(*arguments: str) -> None:
            subprocess.run(arguments, check=True, cwd=cwd)

        host_version = subprocess.check_output(
            [
                str(python),
                "-c",
                "from importlib.metadata import version; print(version('certbot'))",
            ],
            text=True,
            cwd=cwd,
        ).strip()
        check_host = (
            "from importlib.metadata import version\n"
            f"assert version('certbot') == {host_version!r}\n"
        )
        # Remove both packages in this disposable host so installation must pull in the main wheel.
        run(
            "uv",
            "pip",
            "uninstall",
            "--python",
            str(python),
            "certbot-dns-delegation",
            "certbot-dns-alias",
        )
        # Resolve by package name through a local wheel index, exercising the alias dependency.
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-index",
            "--find-links",
            str(ROOT / "dist" / "alias"),
            "--find-links",
            str(ROOT / "dist" / "delegation"),
            "certbot-dns-delegation",
        )
        run(str(python), "-c", CHECK_ALIAS + CHECK_PLUGIN + check_host)
        run(
            str(python),
            "-c",
            CERTBOT_CLI,
            "plugins",
            "--text",
            "--config-dir",
            str(cwd / "config"),
            "--work-dir",
            str(cwd / "work"),
            "--logs-dir",
            str(cwd / "logs"),
        )
        run("uv", "pip", "uninstall", "--python", str(python), "certbot-dns-delegation")
        run(str(python), "-c", CHECK_REMOVED + CHECK_PLUGIN + check_host)


if __name__ == "__main__":
    main()
