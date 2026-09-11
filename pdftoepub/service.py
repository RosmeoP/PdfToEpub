from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.pdftoepub.serve"
PLIST_NAME = f"{LABEL}.plist"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

_DEFAULT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>__LABEL__</string>
  <key>ProgramArguments</key>
  <array>
__PROGRAM_ARGUMENTS__
  </array>
  <key>WorkingDirectory</key>
  <string>__WORKING_DIRECTORY__</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>StandardOutPath</key>
  <string>__STDOUT_PATH__</string>
  <key>StandardErrorPath</key>
  <string>__STDERR_PATH__</string>
</dict>
</plist>
"""


def default_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def template_path() -> Path:
    return project_root() / "launchd" / PLIST_NAME


def service_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}"


def resolve_program_arguments(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> list[str]:
    root = project_root()
    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return [str(venv_python), "-m", "pdftoepub", "serve", "--host", host, "--port", str(port)]
    venv_script = root / ".venv" / "bin" / "pdftoepub"
    if venv_script.is_file():
        return [str(venv_script), "serve", "--host", host, "--port", str(port)]
    script = shutil.which("pdftoepub")
    if script:
        return [script, "serve", "--host", host, "--port", str(port)]
    return [sys.executable, "-m", "pdftoepub", "serve", "--host", host, "--port", str(port)]


def working_directory() -> Path:
    root = project_root()
    if (root / "pyproject.toml").is_file() and (root / "pdftoepub").is_dir():
        return root
    return Path.home()


def plist_destination(dest: Path | str | None) -> Path:
    if dest is None:
        return default_agents_dir() / PLIST_NAME
    dest = Path(dest)
    if dest.suffix == ".plist":
        return dest
    return dest / PLIST_NAME


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_template() -> str:
    path = template_path()
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return _DEFAULT_TEMPLATE


def render_plist(
    *,
    program_arguments: list[str] | None = None,
    workdir: Path | None = None,
    log_dir: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> str:
    args = program_arguments if program_arguments is not None else resolve_program_arguments(host=host, port=port)
    workdir = workdir or working_directory()
    log_dir = log_dir or (Path.home() / "Library" / "Logs" / "pdftoepub")
    arg_xml = "\n".join(f"    <string>{_xml_escape(arg)}</string>" for arg in args)
    return (
        _load_template()
        .replace("__LABEL__", _xml_escape(LABEL))
        .replace("__PROGRAM_ARGUMENTS__", arg_xml)
        .replace("__WORKING_DIRECTORY__", _xml_escape(str(workdir)))
        .replace("__STDOUT_PATH__", _xml_escape(str(log_dir / "serve.log")))
        .replace("__STDERR_PATH__", _xml_escape(str(log_dir / "serve.err")))
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def load_agent(plist: Path) -> None:
    plist = Path(plist)
    unload_agent(plist)
    result = subprocess.run(
        ["launchctl", "bootstrap", _domain(), str(plist)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").lower()
    if "already" in stderr:
        return
    subprocess.run(["launchctl", "load", "-w", str(plist)], check=False, capture_output=True)


def unload_agent(plist: Path) -> None:
    plist = Path(plist)
    subprocess.run(["launchctl", "bootout", _domain(), str(plist)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], check=False, capture_output=True)
    subprocess.run(["launchctl", "unload", "-w", str(plist)], check=False, capture_output=True)


def install_service(
    *,
    dest: Path | str | None = None,
    load: bool = True,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    program_arguments: list[str] | None = None,
) -> Path:
    plist_path = plist_destination(dest)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if dest is None:
        log_dir = Path.home() / "Library" / "Logs" / "pdftoepub"
    else:
        log_dir = plist_path.parent / "logs"
    if dest is not None or load:
        log_dir.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        render_plist(
            program_arguments=program_arguments,
            workdir=working_directory(),
            log_dir=log_dir,
            host=host,
            port=port,
        ),
        encoding="utf-8",
    )
    if load:
        load_agent(plist_path)
    return plist_path


def uninstall_service(*, dest: Path | str | None = None, load: bool = True) -> Path | None:
    plist_path = plist_destination(dest)
    if load and plist_path.exists():
        unload_agent(plist_path)
    if plist_path.exists():
        plist_path.unlink()
        return plist_path
    return None
