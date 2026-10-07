"""Verify alias installation and removal against an isolated Certbot host."""

import argparse
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
CHECK_METADATA = """
import sys
if sys.version_info < (3, 10):
    import importlib_metadata as metadata
else:
    from importlib import metadata
"""
CHECK_PLUGIN = f"""
from pathlib import Path
import certbot_dns_alias
from certbot.plugins.dns_common import DNSAuthenticator

main = metadata.distribution('certbot-dns-alias')
plugins = [p for p in metadata.entry_points(group='certbot.plugins') if p.name == 'dns-alias']
assert len(plugins) == 1, plugins
assert issubclass(plugins[0].load(), DNSAuthenticator)
package = Path(certbot_dns_alias.__file__).resolve()
assert Path({str(ROOT / "certbot_dns_alias")!r}) not in package.parents, package
print('Host loads dns-alias', main.version)
"""
CHECK_ALIAS = """
alias = metadata.distribution('certbot-dns-delegation')
main = metadata.distribution('certbot-dns-alias')
assert alias.version == main.version
assert alias.requires == [f'certbot-dns-alias=={main.version}'], alias.requires
assert not alias.entry_points
assert all('.dist-info/' in str(path) for path in alias.files), alias.files
"""
CHECK_REMOVED = """
try:
    metadata.distribution('certbot-dns-delegation')
except metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError('Alias was not removed')
"""
CERTBOT_CLI = """
cli = next(p for p in metadata.distribution('certbot').entry_points
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
                CHECK_METADATA + "print(metadata.version('certbot'))",
            ],
            text=True,
            cwd=cwd,
        ).strip()
        check_host = f"assert metadata.version('certbot') == {host_version!r}\n"
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
        run(str(python), "-c", CHECK_METADATA + CHECK_ALIAS + CHECK_PLUGIN + check_host)
        result = subprocess.run(
            [
                str(python),
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
        run("uv", "pip", "uninstall", "--python", str(python), "certbot-dns-delegation")
        run(str(python), "-c", CHECK_METADATA + CHECK_REMOVED + CHECK_PLUGIN + check_host)


if __name__ == "__main__":
    main()
