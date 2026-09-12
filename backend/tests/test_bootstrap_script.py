"""Guards for scripts/bootstrap.ps1 that only fail on a real Windows PowerShell run.

These are cheap static checks standing in for things that are expensive or
impossible to catch in CI (which runs on Linux and never executes the script):

* the UTF-8 BOM is load-bearing -- Windows PowerShell 5.1 decodes a .ps1 as the
  system ANSI codepage without one, corrupting every non-ASCII string literal
  and making the script fail to parse at all;
* the Windows version floor is enforced twice, once in PowerShell before Python
  exists and once in launcher.py, and the two must not drift apart.
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

BOOTSTRAP = _REPO_ROOT / "scripts" / "bootstrap.ps1"
START_BAT = _REPO_ROOT / "start.bat"

#: Both gates must refuse anything below this.
WINDOWS_FLOOR = 10


@pytest.fixture(scope="module")
def bootstrap_source() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8-sig")


def test_bootstrap_script_exists():
    assert BOOTSTRAP.is_file(), f"start.bat invokes {BOOTSTRAP}, which is missing"


def test_bootstrap_has_utf8_bom():
    """Without the BOM, powershell.exe (5.1) cannot even parse the script."""
    assert BOOTSTRAP.read_bytes()[:3] == b"\xef\xbb\xbf", (
        "scripts/bootstrap.ps1 lost its UTF-8 BOM. Windows PowerShell 5.1 will "
        "decode its non-ASCII strings as ANSI and fail with parse errors. "
        "Re-save the file as UTF-8 *with* BOM."
    )


def test_bootstrap_non_ascii_round_trips(bootstrap_source):
    """Sanity-check that the localized strings survived the last edit."""
    assert "检测到 Windows" in bootstrap_source


def test_powershell_floor_is_windows_10(bootstrap_source):
    """The floor lives in Test-WindowsVersionSupported now (see
    test_windows_version_check_is_a_pure_testable_function), not inlined in
    Test-SupportedWindows -- check the pure function's own comparison.
    """
    match = re.search(r"\$Version\.Major\s+-ge\s+(\d+)", bootstrap_source)
    assert match, "could not find the Windows version check in bootstrap.ps1"
    assert int(match.group(1)) == WINDOWS_FLOOR


def test_launcher_floor_matches_powershell_floor():
    launcher = importlib.import_module("launcher")
    below = launcher.unsupported_platform_message(
        platform="win32", windows_version=(WINDOWS_FLOOR - 1, 0)
    )
    at_floor = launcher.unsupported_platform_message(
        platform="win32", windows_version=(WINDOWS_FLOOR, 0)
    )
    assert below is not None, "launcher.py accepts a Windows version bootstrap.ps1 rejects"
    assert at_floor is None, "launcher.py rejects the floor bootstrap.ps1 accepts"


def test_every_localized_key_is_defined_and_used(bootstrap_source):
    defined = set(re.findall(r"^\s{4}(\w+)\s*=\s*@\{ en", bootstrap_source, re.M))
    used = set(re.findall(r"Get-Text '(\w+)'", bootstrap_source))
    assert defined, "no localized strings found -- did the table move?"
    assert not (used - defined), f"Get-Text uses undefined keys: {sorted(used - defined)}"
    assert not (defined - used), f"unused localized keys: {sorted(defined - used)}"


def test_checksums_are_never_fetched_from_a_mirror(bootstrap_source):
    """The mirror may supply archive bytes, never the hash used to verify them."""
    for line in bootstrap_source.splitlines():
        if "sha256" in line.lower() and "Invoke-Download" in line:
            assert "officialBase" in line, (
                f"checksum downloaded from a non-official base: {line.strip()!r}"
            )
    # SHASUMS256.txt (Node) must also come from the official host.
    shasums = [ln for ln in bootstrap_source.splitlines() if "SHASUMS256.txt" in ln and "Invoke-Download" in ln]
    assert shasums, "expected Node's checksum to be fetched from SHASUMS256.txt"
    for line in shasums:
        assert "officialBase" in line, f"SHASUMS256.txt fetched from a mirror: {line.strip()!r}"


def test_start_bat_prepends_the_recorded_node_directory():
    """Without this, a project-local Node is invisible to launcher.py."""
    source = START_BAT.read_text(encoding="utf-8", errors="replace")
    assert "node-dir.txt" in source, "start.bat no longer reads the recorded Node directory"
    assert "set \"PATH=" in source, "start.bat no longer prepends Node to PATH"


def test_bootstrap_records_the_node_directory(bootstrap_source):
    assert "node-dir.txt" in bootstrap_source, (
        "bootstrap.ps1 must record the selected Node directory for start.bat"
    )


def test_windows_version_check_is_a_pure_testable_function(bootstrap_source):
    """Version comparison must be a separate function taking a [Version] parameter.

    Without this, the only way to exercise the Windows 7/8/8.1 rejection path is to
    actually run the script on old Windows -- a real machine or VM.  Pulled apart,
    the comparison itself can be verified on any machine by passing a fabricated
    [Version], mirroring launcher.py's unsupported_platform_message(windows_version=...).
    """
    assert re.search(
        r"function Test-WindowsVersionSupported\s*\{", bootstrap_source
    ), "Test-WindowsVersionSupported is missing -- version comparison must not be inlined"
    assert "[Version]$Version" in bootstrap_source, (
        "Test-WindowsVersionSupported must take an explicit [Version] parameter"
    )
    # Test-SupportedWindows must actually call it, not just read the environment inline.
    supported_windows_block = re.search(
        r"function Test-SupportedWindows\s*\{.*?\n\}", bootstrap_source, re.S
    )
    assert supported_windows_block, "Test-SupportedWindows function not found"
    assert "Test-WindowsVersionSupported" in supported_windows_block.group(0), (
        "Test-SupportedWindows no longer delegates to the pure, testable comparison"
    )


def test_server_core_and_architecture_detection_exist(bootstrap_source):
    """Guards the two version-floor blind spots: ARM64 and Windows Server Core.

    The numeric Windows-10 floor alone accepts Server 2016+ (they all report
    Major=10) but says nothing about architecture or edition. ARM64 runs the x64
    downloads under emulation (unverified, not blocked); Server Core is missing
    Desktop Experience components Playwright Chromium is documented to need
    (blocked, since no download fixes a missing DLL).
    """
    assert re.search(r"function Test-WindowsServerCore\s*\{", bootstrap_source)
    assert re.search(r"function Get-WindowsArchitecture\s*\{", bootstrap_source)
    assert "ServerLevels" in bootstrap_source and "ServerCore" in bootstrap_source, (
        "expected the documented Server Core registry marker"
    )
    supported_windows_block = re.search(
        r"function Test-SupportedWindows\s*\{.*?\n\}", bootstrap_source, re.S
    ).group(0)
    assert "Test-WindowsServerCore" in supported_windows_block, (
        "Test-SupportedWindows must actually call the Server Core check"
    )
    assert "Get-WindowsArchitecture" in supported_windows_block, (
        "Test-SupportedWindows must actually call the architecture check"
    )
    # Server Core must throw (hard block); ARM64 must only warn (Write-Host), not throw.
    arm_line = next(
        (ln for ln in supported_windows_block.splitlines() if "Arm64" in ln), None
    )
    assert arm_line, "no ARM64 branch found in Test-SupportedWindows"
    # The `if (Test-WindowsServerCore) { ... }` body must contain a throw before
    # the next top-level `if` in the function -- i.e. it's a hard block, not
    # merely logged and fallen through.
    after_check = supported_windows_block.split("Test-WindowsServerCore")[1]
    server_core_if_body = after_check.split("if (", 1)[0]
    assert "throw" in server_core_if_body, "Server Core detection must be a hard block"
