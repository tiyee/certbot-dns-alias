"""Build the implementation and its metadata-only installation alias with uv."""

import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
ALIAS_NAME = "certbot-dns-delegation"


def write_alias_project(source: Path, destination: Path) -> None:
    """Generate a standalone source project with an exact implementation dependency."""
    config = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    main = config["project"]
    project = {
        "name": ALIAS_NAME,
        "version": main["version"],
        "description": f"Installation alias for {main['name']}: {main['description']}",
        "readme": "README.md",
        "requires-python": main["requires-python"],
        "license": main["license"],
        "license-files": main["license-files"],
        "authors": main["authors"],
        "keywords": main["keywords"],
        "classifiers": main["classifiers"],
        "dependencies": [f"{main['name']}=={main['version']}"],
    }
    lines = ["[build-system]"]
    for key, value in config["build-system"].items():
        lines.append(f"{key} = {toml_value(value)}")
    lines.extend(["", "[project]"])
    lines.extend(f"{key} = {toml_value(value)}" for key, value in project.items())
    lines.extend(["", "[project.urls]"])
    lines.extend(f"{key} = {toml_value(value)}" for key, value in main["urls"].items())
    lines.extend(
        [
            "",
            "[tool.hatch.build.targets.wheel]",
            "bypass-selection = true",
            'core-metadata-version = "2.4"',
            "",
            "[tool.hatch.build.targets.sdist]",
            'include = ["/pyproject.toml", "/README.md", "/LICENSE"]',
            'core-metadata-version = "2.4"',
            "",
        ]
    )
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")
    readme = (source / main["readme"]).read_text(encoding="utf-8")
    readme = readme.replace(f"# {main['name']}\n", f"# {ALIAS_NAME}\n", 1)
    (destination / "README.md").write_text(readme, encoding="utf-8")
    shutil.copyfile(source / "LICENSE", destination / "LICENSE")


def toml_value(value: object) -> str:
    """Encode the metadata's strings, arrays and inline tables as TOML."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {toml_value(item)}" for key, item in value.items()) + " }"
    raise TypeError(f"Unsupported metadata value: {value!r}")


def main() -> None:
    with TemporaryDirectory(prefix="certbot-dns-distributions-") as directory:
        staging = Path(directory)
        alias_project = staging / "project"
        write_alias_project(ROOT, alias_project)
        for source, name in [(ROOT, "alias"), (alias_project, "delegation")]:
            subprocess.run(
                ["uv", "build", str(source), "--out-dir", str(staging / name)],
                check=True,
                cwd=ROOT,
            )
        # Replace only these generated output directories, keeping unrelated files intact.
        for name in ["alias", "delegation"]:
            output = ROOT / "dist" / name
            if output.exists():
                shutil.rmtree(output)
            shutil.copytree(staging / name, output)


if __name__ == "__main__":
    main()
