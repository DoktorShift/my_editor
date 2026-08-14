#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Managed R Markdown toolchain: discovery, and installation from official sources.

Knitting an .Rmd needs four components. This module finds what the system
already has and can install what is missing, each from its official source:

- R interpreter        CRAN (cran.r-project.org). macOS: official .pkg via
                       the native admin prompt. Windows: official installer,
                       silent per-user. Linux: the distro's r-base package
                       (the channel CRAN itself directs Linux users to).
- pandoc               official github.com/jgm/pandoc release archive,
                       extracted into the app's toolchain dir; no admin.
                       Handed to rmarkdown via RSTUDIO_PANDOC, exactly how
                       RStudio ships its bundled pandoc.
- rmarkdown R package  installed from CRAN into a PRIVATE app library
                       (R_LIBS_USER); never touches the system library.
- TinyTeX (optional)   official tinytex R package + tinytex::install_tinytex(),
                       enables Knit to PDF.

Nothing is downloaded without the user going through the setup dialog, and
an existing system installation always wins over downloading.

Downloads are HTTPS-only against an allowlist of official hosts. When a
pinned artifact has a known sha256 it is verified; discovery-fetched
artifacts rely on TLS plus the official domain, the same trust anchor as a
manual download. No fabricated checksums: pins without a hash carry None.

Everything lives under ~/.config/my_editor/rmd_toolchain/ (the app already
keeps binary data under ~/.config/my_editor, e.g. blossom_cache).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass

TOOLCHAIN_DIR = os.path.expanduser("~/.config/my_editor/rmd_toolchain")
_STATE_PATH = os.path.join(TOOLCHAIN_DIR, "state.json")
_LIBRARY_DIR = os.path.join(TOOLCHAIN_DIR, "library")
_PANDOC_DIR = os.path.join(TOOLCHAIN_DIR, "pandoc")
_DOWNLOADS_DIR = os.path.join(TOOLCHAIN_DIR, "downloads")

CRAN = "https://cran.r-project.org"
CRAN_PKG_REPO = "https://cloud.r-project.org"

R_DOWNLOAD_PAGE = "https://cran.r-project.org/"
PANDOC_DOWNLOAD_PAGE = "https://pandoc.org/installing.html"

_ALLOWED_HOSTS = {
    "cran.r-project.org",
    "cloud.r-project.org",
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}

# Known-good fallbacks when version discovery fails. The Windows "old/"
# path is stable forever; discovery normally supersedes these.
PINNED_R_VERSION = "4.5.1"
PINNED_PANDOC_VERSION = "3.5"

# sha256 per pinned artifact URL; None means "no published checksum to pin
# against" and TLS + official host is the trust anchor.
PINNED_SHA256: dict[str, str | None] = {}

_UA = {"User-Agent": "my-editor-rmd-toolchain"}


class ToolchainError(Exception):
    """Raised when an install step fails; carries manual instructions."""

    def __init__(self, message: str, instructions: str = "", page: str = ""):
        super().__init__(message)
        self.instructions = instructions
        self.page = page


class Cancelled(Exception):
    """Raised when the user cancels a running install."""


@dataclass
class ComponentStatus:
    present: bool
    source: str    # "system" | "managed" | "missing"
    detail: str    # human-readable path/version note


# --------------------------------------------------------------------------- #
# State                                                                       #
# --------------------------------------------------------------------------- #

def _load_state() -> dict:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(TOOLCHAIN_DIR, exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# --------------------------------------------------------------------------- #
# Discovery                                                                   #
# --------------------------------------------------------------------------- #

def _version_key(path: str):
    m = re.search(r"R-(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(g) for g in m.groups()) if m else (0, 0, 0)


def find_rscript() -> str | None:
    """Locate Rscript: PATH first, then the standard per-OS install spots."""
    found = shutil.which("Rscript")
    if found:
        return found

    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates = [
            "/Library/Frameworks/R.framework/Resources/bin/Rscript",
            "/opt/homebrew/bin/Rscript",
            "/usr/local/bin/Rscript",
        ]
    elif sys.platform == "win32":
        pattern_all = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                   "R", "R-*", "bin", "Rscript.exe")
        pattern_user = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                    "Programs", "R", "R-*", "bin", "Rscript.exe")
        versioned = sorted(glob.glob(pattern_all) + glob.glob(pattern_user),
                           key=_version_key, reverse=True)
        candidates = versioned
    else:
        candidates = ["/usr/bin/Rscript", "/usr/local/bin/Rscript"]

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def find_pandoc() -> tuple[str | None, str]:
    """Return (pandoc bin dir, source). Managed copy wins over PATH so the
    version we tested against is the one rmarkdown uses."""
    state = _load_state()
    managed = state.get("pandoc", {}).get("bin")
    if managed and os.path.isfile(managed):
        return os.path.dirname(managed), "managed"
    found = shutil.which("pandoc")
    if found:
        return os.path.dirname(found), "system"
    return None, "missing"


def _run_r(rscript: str, expr: str, timeout: int = 60,
           extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["R_LIBS_USER"] = _LIBRARY_DIR
    if extra_env:
        env.update(extra_env)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run([rscript, "-e", expr], capture_output=True,
                          text=True, timeout=timeout, env=env, **kwargs)


def _r_package_present(rscript: str, package: str) -> bool:
    try:
        proc = _run_r(rscript, f"cat(system.file(package='{package}'))")
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _latex_available() -> tuple[bool, str]:
    for binary in ("pdflatex", "xelatex"):
        found = shutil.which(binary)
        if found:
            return True, found
    for root in ("~/Library/TinyTeX", "~/.TinyTeX",
                 os.path.join(os.environ.get("APPDATA", ""), "TinyTeX")):
        expanded = os.path.expanduser(root)
        if expanded and os.path.isdir(expanded):
            return True, expanded
    return False, ""


def status(check_r_packages: bool = True) -> dict[str, ComponentStatus]:
    """Report each component. With check_r_packages this runs Rscript
    synchronously (fast, but call it off the UI thread)."""
    result: dict[str, ComponentStatus] = {}

    rscript = find_rscript()
    if rscript:
        result["r"] = ComponentStatus(True, "system", rscript)
    else:
        result["r"] = ComponentStatus(False, "missing", "")

    pandoc_dir, source = find_pandoc()
    result["pandoc"] = ComponentStatus(pandoc_dir is not None, source,
                                       pandoc_dir or "")

    if rscript and check_r_packages:
        has_rmd = _r_package_present(rscript, "rmarkdown")
        result["rmarkdown"] = ComponentStatus(
            has_rmd, "managed" if has_rmd else "missing",
            _LIBRARY_DIR if has_rmd else "")
    else:
        result["rmarkdown"] = ComponentStatus(False, "missing", "")

    has_latex, where = _latex_available()
    result["latex"] = ComponentStatus(has_latex,
                                      "system" if has_latex else "missing",
                                      where)
    return result


def ready_to_knit(to_pdf: bool = False) -> bool:
    s = status()
    base = s["r"].present and s["pandoc"].present and s["rmarkdown"].present
    return base and (s["latex"].present if to_pdf else True)


def knit_environment(env):
    """Augment a QProcessEnvironment for KnitRunner."""
    pandoc_dir, _source = find_pandoc()
    if pandoc_dir:
        env.insert("RSTUDIO_PANDOC", pandoc_dir)
        env.insert("PATH", pandoc_dir + os.pathsep + env.value("PATH", ""))
    os.makedirs(_LIBRARY_DIR, exist_ok=True)
    env.insert("R_LIBS_USER", _LIBRARY_DIR)
    return env


# --------------------------------------------------------------------------- #
# Downloads                                                                   #
# --------------------------------------------------------------------------- #

def _check_host(url: str) -> None:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ToolchainError(f"refusing non-official download URL: {url}")


def _fetch_text(url: str, timeout: int = 30) -> str:
    _check_host(url)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        _check_host(resp.geturl())
        return resp.read().decode("utf-8", errors="replace")


def _download(url: str, dest: str, progress=None, cancel=None,
              sha256: str | None = None) -> str:
    """Stream url to dest. progress(done_bytes, total_bytes); cancel is a
    threading.Event-like object. Verifies sha256 when given."""
    _check_host(url)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers=_UA)
    hasher = hashlib.sha256()
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=60) as resp:
        _check_host(resp.geturl())
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    f.close()
                    os.unlink(tmp)
                    raise Cancelled()
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if sha256 is not None and hasher.hexdigest().lower() != sha256.lower():
        os.unlink(tmp)
        raise ToolchainError(
            f"checksum mismatch for {os.path.basename(dest)}; download "
            "discarded. Try again, or install manually.",
            page=R_DOWNLOAD_PAGE)
    os.replace(tmp, dest)
    return dest


# --------------------------------------------------------------------------- #
# Version discovery (official pages), with pinned fallbacks                   #
# --------------------------------------------------------------------------- #

def discover_r_version() -> str:
    try:
        page = _fetch_text(f"{CRAN}/bin/windows/base/release.html")
        m = re.search(r"R-(\d+\.\d+\.\d+)-win\.exe", page)
        if m:
            return m.group(1)
    except (OSError, ToolchainError):
        pass
    return PINNED_R_VERSION


def discover_pandoc_version() -> str:
    try:
        raw = _fetch_text("https://api.github.com/repos/jgm/pandoc/releases/latest")
        tag = json.loads(raw).get("tag_name", "")
        if re.fullmatch(r"\d+(\.\d+)+", tag):
            return tag
    except (OSError, ToolchainError, json.JSONDecodeError):
        pass
    return PINNED_PANDOC_VERSION


def r_installer_url(version: str) -> str:
    if sys.platform == "win32":
        if version == PINNED_R_VERSION:
            return f"{CRAN}/bin/windows/base/old/{version}/R-{version}-win.exe"
        return f"{CRAN}/bin/windows/base/R-{version}-win.exe"
    if sys.platform == "darwin":
        arch = "arm64" if platform.machine() == "arm64" else "x86_64"
        return (f"{CRAN}/bin/macosx/big-sur-{arch}/base/"
                f"R-{version}-{arch}.pkg")
    raise ToolchainError("Linux installs use the distro package manager")


def pandoc_archive_url(version: str) -> str:
    base = f"https://github.com/jgm/pandoc/releases/download/{version}"
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        arch = "arm64" if machine == "arm64" else "x86_64"
        return f"{base}/pandoc-{version}-{arch}-macOS.zip"
    if sys.platform == "win32":
        return f"{base}/pandoc-{version}-windows-x86_64.zip"
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"{base}/pandoc-{version}-linux-{arch}.tar.gz"


# --------------------------------------------------------------------------- #
# Install steps (run these on a worker thread; they block)                    #
# --------------------------------------------------------------------------- #

def install_r(progress=None, log=None, cancel=None) -> str:
    """Install R from CRAN. Returns the Rscript path. May raise
    ToolchainError (with manual instructions) or Cancelled."""
    log = log or (lambda s: None)

    if sys.platform.startswith("linux"):
        return _install_r_linux(log, cancel)

    version = discover_r_version()
    url = r_installer_url(version)
    log(f"Downloading R {version} from CRAN\n  {url}")
    dest = os.path.join(_DOWNLOADS_DIR, os.path.basename(url))
    _download(url, dest, progress=progress, cancel=cancel,
              sha256=PINNED_SHA256.get(url))

    if sys.platform == "darwin":
        log("Installing R (macOS will ask for your password)...")
        script = (f'do shell script "installer -pkg '
                  f'{_sh_quote_applescript(dest)} -target /" '
                  'with administrator privileges')
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise ToolchainError(
                "The R installer did not finish.",
                instructions=f"Open the downloaded package manually:\n{dest}",
                page=R_DOWNLOAD_PAGE)
    else:  # win32
        log("Running the R installer (silent, per-user)...")
        proc = subprocess.run([dest, "/SILENT", "/CURRENTUSER", "/SP-"])
        if proc.returncode != 0:
            log("Silent install refused; launching the interactive installer.")
            proc = subprocess.run([dest])
            if proc.returncode != 0:
                raise ToolchainError(
                    "The R installer did not finish.",
                    instructions=f"Run the downloaded installer manually:\n{dest}",
                    page=R_DOWNLOAD_PAGE)

    rscript = find_rscript()
    if not rscript:
        raise ToolchainError(
            "R was installed but Rscript was not found afterwards.",
            instructions="Restart the editor; if that does not help, "
                         f"install R manually from {R_DOWNLOAD_PAGE}",
            page=R_DOWNLOAD_PAGE)
    log(f"R ready: {rscript}")
    return rscript


def _sh_quote_applescript(path: str) -> str:
    # Quote for the shell INSIDE the AppleScript string; AppleScript quotes
    # are handled by using no special characters in our controlled path.
    return "'" + path.replace("'", "'\\''") + "'"


_LINUX_PM_COMMANDS = [
    ("apt-get", "apt-get update && apt-get install -y r-base"),
    ("dnf", "dnf install -y R"),
    ("pacman", "pacman -S --noconfirm r"),
    ("zypper", "zypper --non-interactive install R-base"),
]


def _install_r_linux(log, cancel=None) -> str:
    pm_cmd = None
    for pm, cmd in _LINUX_PM_COMMANDS:
        if shutil.which(pm):
            pm_cmd = cmd
            break
    if pm_cmd is None:
        raise ToolchainError(
            "No supported package manager was found.",
            instructions="Install R with your distribution's package "
                         "manager (package name: r-base or R).",
            page=R_DOWNLOAD_PAGE)

    if not shutil.which("pkexec"):
        raise ToolchainError(
            "Administrator rights are needed to install R.",
            instructions=f"Run this in a terminal:\n  sudo sh -c '{pm_cmd}'",
            page=R_DOWNLOAD_PAGE)

    log(f"Installing R via the system package manager:\n  {pm_cmd}")
    proc = subprocess.Popen(["pkexec", "sh", "-c", pm_cmd],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise Cancelled()
        log(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        raise ToolchainError(
            "The package manager could not install R.",
            instructions=f"Run this in a terminal:\n  sudo sh -c '{pm_cmd}'",
            page=R_DOWNLOAD_PAGE)

    rscript = find_rscript()
    if not rscript:
        raise ToolchainError("R was installed but Rscript was not found.",
                             page=R_DOWNLOAD_PAGE)
    log(f"R ready: {rscript}")
    return rscript


def install_pandoc(progress=None, log=None, cancel=None) -> str:
    """Download and extract pandoc into the toolchain dir. Returns the
    binary path."""
    log = log or (lambda s: None)
    version = discover_pandoc_version()
    url = pandoc_archive_url(version)
    log(f"Downloading pandoc {version}\n  {url}")
    archive = os.path.join(_DOWNLOADS_DIR, os.path.basename(url))
    _download(url, archive, progress=progress, cancel=cancel,
              sha256=PINNED_SHA256.get(url))

    target = os.path.join(_PANDOC_DIR, version)
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    log("Extracting...")
    binary = extract_pandoc_archive(archive, target)
    if binary is None:
        raise ToolchainError("pandoc archive had an unexpected layout.",
                             page=PANDOC_DOWNLOAD_PAGE)

    state = _load_state()
    state["pandoc"] = {"version": version, "bin": binary}
    _save_state(state)
    log(f"pandoc ready: {binary}")
    return binary


def extract_pandoc_archive(archive: str, target: str) -> str | None:
    """Extract a pandoc release archive and return the pandoc binary path."""
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    else:
        with tarfile.open(archive, "r:gz") as t:
            try:
                # The 'data' filter refuses absolute paths and traversal
                # tricks in the downloaded archive.
                t.extractall(target, filter="data")
            except TypeError:  # Python < 3.12 without the filter argument
                t.extractall(target)

    wanted = "pandoc.exe" if sys.platform == "win32" else "pandoc"
    for root, _dirs, files in os.walk(target):
        if wanted in files:
            binary = os.path.join(root, wanted)
            if sys.platform != "win32":
                os.chmod(binary, 0o755)
            return binary
    return None


def install_rmarkdown(log=None, cancel=None) -> None:
    """Install the rmarkdown package from CRAN into the private library."""
    log = log or (lambda s: None)
    rscript = find_rscript()
    if not rscript:
        raise ToolchainError("R must be installed first.")
    os.makedirs(_LIBRARY_DIR, exist_ok=True)
    lib = _LIBRARY_DIR.replace("\\", "/")
    expr = (f"install.packages('rmarkdown', repos='{CRAN_PKG_REPO}', "
            f"lib='{lib}')")
    log(f"Installing rmarkdown from {CRAN_PKG_REPO} into the app library...")
    _stream_r(rscript, expr, log, cancel)
    if not _r_package_present(rscript, "rmarkdown"):
        raise ToolchainError(
            "rmarkdown did not install cleanly.",
            instructions="In an R console, run:\n"
                         f"  install.packages('rmarkdown', repos='{CRAN_PKG_REPO}')")
    log("rmarkdown ready.")


def install_tinytex(log=None, cancel=None) -> None:
    """Install TinyTeX (LaTeX for Knit to PDF) via the official R package."""
    log = log or (lambda s: None)
    rscript = find_rscript()
    if not rscript:
        raise ToolchainError("R must be installed first.")
    os.makedirs(_LIBRARY_DIR, exist_ok=True)
    lib = _LIBRARY_DIR.replace("\\", "/")
    log("Installing TinyTeX (this downloads roughly 100 MB)...")
    expr = (f"if (!nzchar(system.file(package='tinytex'))) "
            f"install.packages('tinytex', repos='{CRAN_PKG_REPO}', lib='{lib}'); "
            f"tinytex::install_tinytex(force = TRUE)")
    _stream_r(rscript, expr, log, cancel)
    has_latex, _where = _latex_available()
    if not has_latex:
        raise ToolchainError(
            "TinyTeX did not install cleanly.",
            instructions="In an R console, run:\n"
                         "  install.packages('tinytex'); tinytex::install_tinytex()")
    log("TinyTeX ready.")


def _stream_r(rscript: str, expr: str, log, cancel=None) -> None:
    env = os.environ.copy()
    env["R_LIBS_USER"] = _LIBRARY_DIR
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([rscript, "-e", expr], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env,
                            **kwargs)
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise Cancelled()
        log(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        raise ToolchainError("R exited with an error; see the log above.")
