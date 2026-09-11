from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def pick_pdfs() -> list[Path]:
    if sys.platform == "darwin":
        chosen = _macos_choose(
            'choose file with prompt "Choose PDF files to convert" '
            'of type {"com.adobe.pdf"} with multiple selections allowed'
        )
        if chosen is not None:
            return chosen
    return _tk_pick_files()


def pick_folder(prompt: str = "Choose a folder of PDFs") -> Path | None:
    if sys.platform == "darwin":
        chosen = _macos_choose(f'choose folder with prompt "{prompt}"')
        if chosen:
            return chosen[0]
        if chosen is not None:
            return None
    return _tk_pick_folder(prompt)


def reveal(path: Path) -> None:
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
        return
    if sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(path)], check=False)
        return
    subprocess.run(["xdg-open", str(path.parent)], check=False)


def open_in_books(path: Path) -> None:
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Books", str(path)], check=False)
        return
    reveal(path)


def notify(title: str, message: str) -> None:
    if sys.platform == "darwin":
        script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def _macos_choose(apple_script: str) -> list[Path] | None:
    script = f"""
    set theItems to {apple_script}
    set out to ""
    if class of theItems is list then
        repeat with f in theItems
            set out to out & POSIX path of f & linefeed
        end repeat
    else
        set out to POSIX path of theItems
    end if
    return out
    """
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return [] if "User canceled" in (result.stderr or "") else None
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def _tk_pick_files() -> list[Path]:
    try:
        from tkinter import Tk, filedialog
    except Exception:
        return []
    root = Tk()
    root.withdraw()
    root.call("wm", "attributes", ".", "-topmost", True)
    names = filedialog.askopenfilenames(title="Choose PDF files to convert", filetypes=[("PDF", "*.pdf")])
    root.destroy()
    return [Path(name) for name in names]


def _tk_pick_folder(prompt: str) -> Path | None:
    try:
        from tkinter import Tk, filedialog
    except Exception:
        return None
    root = Tk()
    root.withdraw()
    root.call("wm", "attributes", ".", "-topmost", True)
    name = filedialog.askdirectory(title=prompt)
    root.destroy()
    return Path(name) if name else None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
