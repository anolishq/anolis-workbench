"""Preflight checks for target machine readiness.

Verifies that the target is suitable for installing Anolis components.
Runs via the Executor abstraction — works identically for local and remote targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from anolis_workbench.core.executor import Executor, LocalExecutor


@dataclass
class CheckResult:
    """Result of a single preflight check."""

    name: str
    passed: bool
    detail: str
    fatal: bool
    fix_hint: str | None = None


@dataclass
class PreflightResult:
    """Aggregate result of all preflight checks."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no fatal checks failed."""
        return not any(c.fatal and not c.passed for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        """True if any non-fatal checks failed."""
        return any(not c.fatal and not c.passed for c in self.checks)


def _check_architecture(executor: Executor) -> CheckResult:
    """Check that the target architecture is supported."""
    result = executor.run(["uname", "-m"])
    if result.returncode != 0:
        return CheckResult(
            name="Architecture",
            passed=False,
            detail="Could not detect architecture",
            fatal=True,
        )
    arch = result.stdout.strip()
    supported = ("aarch64", "arm64", "x86_64")
    if arch in supported:
        return CheckResult(name="Architecture", passed=True, detail=arch, fatal=True)
    return CheckResult(
        name="Architecture",
        passed=False,
        detail=f"{arch} (unsupported)",
        fatal=True,
        fix_hint=f"Supported architectures: {', '.join(supported)}",
    )


def _check_i2c_enabled(executor: Executor) -> CheckResult:
    """Check that I2C bus is available."""
    result = executor.run(["ls", "/dev/i2c-1"])
    if result.returncode == 0:
        return CheckResult(name="I2C enabled", passed=True, detail="/dev/i2c-1", fatal=True)
    return CheckResult(
        name="I2C enabled",
        passed=False,
        detail="/dev/i2c-1 not found",
        fatal=True,
        fix_hint="Enable I2C: sudo raspi-config → Interface Options → I2C → Enable",
    )


def _check_i2c_permissions(executor: Executor) -> CheckResult:
    """Check that the current user has I2C group membership."""
    result = executor.run(["groups"])
    if result.returncode != 0:
        return CheckResult(
            name="I2C permissions",
            passed=False,
            detail="Could not list groups",
            fatal=False,
            fix_hint="sudo usermod -aG i2c $USER && logout/login",
        )
    groups = result.stdout.strip()
    if "i2c" in groups.split():
        user_part = groups.split(":")[0] if ":" in groups else ""
        return CheckResult(
            name="I2C permissions",
            passed=True,
            detail=f"{user_part} ∈ i2c" if user_part else "user ∈ i2c",
            fatal=False,
        )
    return CheckResult(
        name="I2C permissions",
        passed=False,
        detail="User not in i2c group",
        fatal=False,
        fix_hint="sudo usermod -aG i2c $USER && logout/login",
    )


def _check_disk_space(executor: Executor, path: str = "/usr/local") -> CheckResult:
    """Check that at least 50 MB is free at the install prefix."""
    result = executor.run(["df", "--output=avail", "-B1", path])
    if result.returncode != 0:
        return CheckResult(
            name="Disk space",
            passed=False,
            detail=f"Could not check disk space at {path}",
            fatal=True,
        )
    lines = result.stdout.strip().splitlines()
    if len(lines) < 2:
        return CheckResult(name="Disk space", passed=False, detail="Unexpected df output", fatal=True)
    try:
        avail_bytes = int(lines[-1].strip())
    except ValueError:
        return CheckResult(name="Disk space", passed=False, detail="Could not parse df output", fatal=True)

    min_bytes = 50 * 1024 * 1024  # 50 MB
    avail_mb = avail_bytes / (1024 * 1024)
    if avail_bytes >= min_bytes:
        if avail_mb >= 1024:
            detail = f"{avail_mb / 1024:.1f} GB free"
        else:
            detail = f"{avail_mb:.0f} MB free"
        return CheckResult(name="Disk space", passed=True, detail=detail, fatal=True)
    return CheckResult(
        name="Disk space",
        passed=False,
        detail=f"{avail_mb:.0f} MB free (need ≥50 MB)",
        fatal=True,
        fix_hint="Free up space on the target's filesystem",
    )


def _check_python(executor: Executor) -> CheckResult:
    """Check Python 3.10+ is available."""
    result = executor.run(["python3", "--version"])
    if result.returncode != 0:
        return CheckResult(
            name="Python",
            passed=False,
            detail="python3 not found",
            fatal=False,
            fix_hint="Install Python 3.10+: sudo apt-get install python3",
        )
    version_str = result.stdout.strip()  # "Python 3.11.2"
    parts = version_str.replace("Python ", "").split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return CheckResult(name="Python", passed=False, detail=f"Could not parse: {version_str}", fatal=False)

    if major >= 3 and minor >= 10:
        patch = parts[2] if len(parts) > 2 else "0"
        return CheckResult(name="Python", passed=True, detail=f"{major}.{minor}.{patch}", fatal=False)
    return CheckResult(
        name="Python",
        passed=False,
        detail=f"{version_str} (need ≥3.10)",
        fatal=False,
        fix_hint="Upgrade Python to 3.10+",
    )


def _check_sudo(executor: Executor) -> CheckResult:
    """Check if sudo is available (NOPASSWD preferred but not required)."""
    result = executor.run(["sudo", "-n", "true"])
    if result.returncode == 0:
        return CheckResult(name="Sudo", passed=True, detail="NOPASSWD configured", fatal=False)
    return CheckResult(
        name="Sudo",
        passed=False,
        detail="password required (NOPASSWD not configured)",
        fatal=False,
        fix_hint=(
            "Add to /etc/sudoers.d/anolis-provision:\n"
            "      $USER ALL=(ALL) NOPASSWD: /bin/tar, /usr/bin/tar, /bin/mv, "
            "/usr/bin/mv, /bin/systemctl, /usr/bin/systemctl"
        ),
    )


def run_preflight(
    executor: Executor | None = None,
    *,
    install_prefix: str = "/usr/local",
) -> PreflightResult:
    """Run all preflight checks on the target.

    Args:
        executor: Executor to use for running checks. Defaults to LocalExecutor.
        install_prefix: Path to check disk space for.

    Returns:
        PreflightResult with all check outcomes.
    """
    if executor is None:
        executor = LocalExecutor()

    checks = [
        _check_architecture(executor),
        _check_i2c_enabled(executor),
        _check_i2c_permissions(executor),
        _check_disk_space(executor, install_prefix),
        _check_python(executor),
        _check_sudo(executor),
    ]
    return PreflightResult(checks=checks)


def format_preflight_result(result: PreflightResult, *, target: str = "localhost") -> str:
    """Format preflight results for terminal output.

    Args:
        result: PreflightResult to format.
        target: Target label for the header (e.g. "pi@192.168.1.10").

    Returns:
        Formatted multi-line string.
    """
    lines = [f"Preflight — {target}"]
    for check in result.checks:
        icon = "✓" if check.passed else "✗"
        lines.append(f"  {icon} {check.name}: {check.detail}")
        if not check.passed and check.fix_hint:
            for hint_line in check.fix_hint.splitlines():
                lines.append(f"    → {hint_line}")
    return "\n".join(lines)
