r"""Build the Windows launcher and assemble a clean, installable release zip.

Run with the Windows build environment's Python from the repository root:
    .venv\Scripts\python scripts\build_windows_release.py
"""

import json
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "release"
DIST = ROOT / "dist"


def release_version() -> str:
    version = json.loads((ROOT / "plugin" / "package.json").read_text("utf-8"))["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("plugin/package.json has an invalid release version")
    launcher = (ROOT / "launcher" / "app.py").read_text("utf-8")
    match = re.search(r'^VERSION = "([^"]+)"$', launcher, re.MULTILINE)
    if not match or match.group(1) != version:
        raise ValueError("launcher/app.py and plugin/package.json versions differ")
    return version


def copy_files(source: Path, target: Path, names: list[str]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(source / name, target / name)


def stage_release(exe: Path, stage: Path) -> None:
    """Copy only runtime files; never include a development venv or model."""
    if not exe.is_file():
        raise ValueError(f"missing or invalid Windows executable: {exe}")
    with exe.open("rb") as executable:
        if executable.read(2) != b"MZ":
            raise ValueError(f"missing or invalid Windows executable: {exe}")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(exe, stage / "Spriteloom.exe")
    copy_files(ROOT, stage, ["README.md", "LICENSE", "start-server.bat", "install-plugin.bat"])
    copy_files(ROOT / "server", stage / "server",
               [p.name for p in (ROOT / "server").glob("*.py")] + ["requirements.txt"])
    # MCP autostart imports launcher.server_proc from the extracted folder.
    copy_files(ROOT / "launcher", stage / "launcher", ["__init__.py", "server_proc.py"])
    copy_files(ROOT / "plugin", stage / "plugin",
               [p.name for p in (ROOT / "plugin").iterdir() if p.is_file()])
    copy_files(ROOT / "mcp", stage / "mcp", ["pyproject.toml", "README.md"])
    copy_files(ROOT / "mcp" / "spriteloom_mcp", stage / "mcp" / "spriteloom_mcp",
               [p.name for p in (ROOT / "mcp" / "spriteloom_mcp").glob("*.py")])


def write_zip(stage: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                archive.write(file, Path(stage.name) / file.relative_to(stage))
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"release archive failed integrity check: {bad}")


def main() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Build the Windows x64 release on Windows x64")

    version = release_version()
    exe = WORK / "pyinstaller-dist" / "Spriteloom.exe"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(WORK / "pyinstaller-dist"),
        "--workpath", str(WORK / "pyinstaller-work"),
        str(ROOT / "build.spec"),
    ], cwd=ROOT, check=True)

    stage = WORK / "stage" / "Spriteloom"
    stage_release(exe, stage)
    output = DIST / f"Spriteloom-{version}-windows-x64.zip"
    write_zip(stage, output)
    print(f"Created {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
