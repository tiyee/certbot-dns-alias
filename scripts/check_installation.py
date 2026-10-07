"""Verify alias installation and removal against an isolated Certbot host."""

import argparse
import subprocess
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

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
extras = main.metadata.get_all('Provides-Extra') or []
assert (alias.metadata.get_all('Provides-Extra') or []) == extras
expected = [f'certbot-dns-alias=={main.version}'] + [
    f"certbot-dns-alias[{extra}]=={main.version}; extra == '{extra}'" for extra in extras
]
normalize = lambda requirement: requirement.replace('"', "'").replace(' ', '')
assert sorted(map(normalize, alias.requires)) == sorted(map(normalize, expected)), alias.requires
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
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Create a fresh host with the same Certbot/ACME versions and run the full test suite",
    )
    # Keep the venv interpreter's symlink path so Python retains its host environment.
    arguments = parser.parse_args()
    python = arguments.python.absolute()
    wheels = list((ROOT / "dist" / "alias").glob("*.whl"))
    if len(wheels) != 1:
        parser.error("Build both distributions first; expected exactly one main wheel")
    with ZipFile(wheels[0]) as wheel:
        metadata_path = next(name for name in wheel.namelist() if name.endswith("/METADATA"))
        built_version = BytesParser().parsebytes(wheel.read(metadata_path))["Version"]
    with TemporaryDirectory(prefix="certbot-dns-install-check-") as directory:
        cwd = Path(directory)

        def run(*arguments: str) -> None:
            subprocess.run(arguments, check=True, cwd=cwd)

        host_versions = (
            subprocess.check_output(
                [
                    str(python),
                    "-c",
                    CHECK_METADATA
                    + "print(metadata.version('certbot')); print(metadata.version('acme'))",
                ],
                text=True,
                cwd=cwd,
            )
            .strip()
            .splitlines()
        )
        host_version, acme_version = host_versions
        legacy = host_version.split(".", 1)[0] == "3"
        if arguments.fresh:
            environment = cwd / "host"
            run("uv", "venv", "--python", str(python), str(environment))
            # Venv layout differs on Windows; do not resolve the interpreter symlink.
            interpreter = "Scripts/python.exe" if python.suffix == ".exe" else "bin/python"
            python = environment / interpreter
            dependencies = [
                f"certbot=={host_version}",
                f"acme=={acme_version}",
                "pytest>=8,<10",
            ]
            if not legacy:
                dependencies.append("pyopenssl>=25")
            # Deliberately omit the legacy TLS cap here. The plugin's installation
            # extra must establish it, even when a fresh host selected a newer version.
            run("uv", "pip", "install", "--python", str(python), *dependencies)
        check_host = (
            f"assert metadata.version('certbot') == {host_version!r}\n"
            f"assert metadata.version('acme') == {acme_version!r}\n"
            f"assert main.version == {built_version!r}\n"
        )
        if arguments.fresh:
            check_host += (
                "assert int(metadata.version('pyopenssl').split('.')[0]) "
                + ("< 25" if legacy else ">= 25")
                + "\n"
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
            # Local wheels may be rebuilt without changing their development version.
            "--no-cache",
            "--python",
            str(python),
            # Fresh hosts must resolve the extra's legacy TLS dependencies from PyPI.
            *([] if arguments.fresh else ["--no-index"]),
            "--find-links",
            str(ROOT / "dist" / "alias"),
            "--find-links",
            str(ROOT / "dist" / "delegation"),
            f"certbot-dns-delegation{'[certbot3]' if legacy else ''}=={built_version}",
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
        if arguments.fresh:
            run(
                str(python),
                "-m",
                "pytest",
                "--import-mode=importlib",
                "-q",
                str(ROOT / "tests"),
            )


if __name__ == "__main__":
    main()
