#!/usr/bin/env python3
"""Linux administration lab: measurements, structured logs, and recoverable backups."""

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid


class LabError(Exception):
    """A failed operation that must never be reported as healthy."""


class BusyError(LabError):
    pass


REQUIRED = {
    "cpu_threshold", "memory_threshold", "disk_threshold", "sample_seconds",
    "disk_paths", "services", "log_dir", "state_dir", "backup_dir",
    "backup_source", "keep_backups", "max_backup_bytes", "backup_max_age_hours",
}
ARCHIVE_NAME = re.compile(r"backup-\d{8}T\d{12}Z-[0-9a-f]{8}\.tar\.gz")


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_config(filename):
    filename = Path(filename).resolve()
    try:
        cfg = json.loads(filename.read_text())
    except (OSError, ValueError) as exc:
        raise LabError(f"Cannot read configuration: {exc}") from exc
    if not isinstance(cfg, dict) or set(cfg) != REQUIRED:
        raise LabError("Configuration keys must match config/local.json exactly")
    for key in ("cpu_threshold", "memory_threshold", "disk_threshold"):
        value = cfg[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            raise LabError(f"{key} must be a number between 0 and 100")
    for key, low, high in (
        ("sample_seconds", 0.05, 30), ("backup_max_age_hours", 0.01, 8760),
        ("keep_backups", 1, 365), ("max_backup_bytes", 1, 10 * 1024**3),
    ):
        value = cfg[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise LabError(f"{key} must be between {low} and {high}")
    for key in ("keep_backups", "max_backup_bytes"):
        if type(cfg[key]) is not int:
            raise LabError(f"{key} must be an integer")
    for key in ("services", "disk_paths"):
        if not isinstance(cfg[key], list) or len(cfg[key]) > 16:
            raise LabError(f"{key} must be a list with at most 16 entries")
    if not cfg["disk_paths"]:
        raise LabError("At least one disk path is required")
    for unit in cfg["services"]:
        if not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", unit):
            raise LabError("Service names must be explicit .service units")
    for key in ("log_dir", "state_dir", "backup_dir", "backup_source"):
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            raise LabError(f"{key} must be a nonempty path")
        cfg[key] = (filename.parent / cfg[key]).resolve()
    paths = []
    for value in cfg["disk_paths"]:
        if not isinstance(value, str) or not value.strip():
            raise LabError("Disk paths must be nonempty strings")
        paths.append((filename.parent / value).resolve())
    cfg["disk_paths"] = paths
    source = cfg["backup_source"]
    if source == Path("/") or source == cfg["backup_dir"]:
        raise LabError("The backup source must be a dedicated data directory")
    for key in ("backup_dir", "state_dir", "log_dir"):
        if cfg[key].is_relative_to(source) or source.is_relative_to(cfg[key]):
            raise LabError(f"{key} must be separate from the backup source")
    if len({cfg[k] for k in ("log_dir", "state_dir", "backup_dir")}) != 3:
        raise LabError("Log, state, and backup directories must be distinct")
    return cfg


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o640)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def event(cfg, level, message, **fields):
    entry = {"time": timestamp(), "level": level, "message": message, **fields}
    line = json.dumps(entry, sort_keys=True, allow_nan=False) + "\n"
    directory = cfg["log_dir"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    # Reopen for each record so rename/create log rotation needs no daemon restart.
    fd = os.open(directory / "operations.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    with os.fdopen(fd, "w") as handle:
        handle.write(line)
    print(line, end="", flush=True)  # Captured by journald in installed services.


@contextlib.contextmanager
def lock(cfg, name):
    cfg["state_dir"].mkdir(parents=True, exist_ok=True, mode=0o750)
    with (cfg["state_dir"] / f"{name}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyError(f"Another {name} operation is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_cpu():
    fields = Path("/proc/stat").read_text().splitlines()[0].split()
    if fields[0] != "cpu" or len(fields) < 9:
        raise LabError("Invalid aggregate CPU counters")
    values = [int(value) for value in fields[1:9]]
    # guest/guest_nice already belong to user/nice: exclude them from the sum.
    return sum(values), values[3] + values[4]


def cpu_percent(seconds):
    before_total, before_idle = read_cpu()
    time.sleep(seconds)
    after_total, after_idle = read_cpu()
    elapsed, idle = after_total - before_total, after_idle - before_idle
    if elapsed <= 0 or not 0 <= idle <= elapsed:
        raise LabError("CPU counters did not advance consistently")
    return round(100 * (elapsed - idle) / elapsed, 2)


def memory_percent():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        values[name] = int(value.split()[0])
    total, available = values["MemTotal"], values["MemAvailable"]
    if total <= 0 or not 0 <= available <= total:
        raise LabError("Invalid memory counters")
    return round(100 * (total - available) / total, 2)


def disk_percent(path):
    usage = shutil.disk_usage(path)
    if usage.total <= 0:
        raise LabError(f"Invalid filesystem size for {path}")
    return round(usage.used * 100 / usage.total, 2)


def check_service(unit):
    result = subprocess.run(
        ["systemctl", "show", "--no-pager", "--property=LoadState,ActiveState,SubState", unit],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if result.returncode:
        raise LabError(f"Cannot query {unit}: {result.stderr.strip() or result.returncode}")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if not {"LoadState", "ActiveState", "SubState"} <= values.keys():
        raise LabError(f"Incomplete systemd response for {unit}")
    ok = values["LoadState"] == "loaded" and values["ActiveState"] == "active"
    return {"name": unit, "status": "ok" if ok else "warning", **values}


def backups(cfg):
    return sorted(
        (p for p in cfg["backup_dir"].glob("backup-*.tar.gz") if ARCHIVE_NAME.fullmatch(p.name)),
        reverse=True,
    )


def backup_freshness(cfg):
    candidates = backups(cfg)
    if not candidates:
        return {"name": "backup_age", "status": "warning", "detail": "No completed backup"}
    archive = candidates[0]
    if not archive.with_suffix(archive.suffix + ".sha256").is_file():
        raise LabError("Latest backup has no checksum")
    age = max(0, (time.time() - archive.stat().st_mtime) / 3600)
    return {
        "name": "backup_age", "status": "warning" if age > cfg["backup_max_age_hours"] else "ok",
        "hours": round(age, 3), "limit_hours": cfg["backup_max_age_hours"],
        "archive": archive.name,
    }


def monitor(cfg):
    checks = []

    def measure(name, fn, threshold=None):
        try:
            value = fn()
            if threshold is None:
                checks.append(value)
            else:
                if not math.isfinite(value) or not 0 <= value <= 100:
                    raise LabError("Measurement is outside the percentage range")
                checks.append({"name": name, "percent": value, "threshold": threshold,
                               "status": "warning" if value >= threshold else "ok"})
        except (OSError, ValueError, KeyError, LabError, subprocess.SubprocessError) as exc:
            checks.append({"name": name, "status": "error", "detail": str(exc)})

    with lock(cfg, "monitor"):
        measure("cpu", lambda: cpu_percent(cfg["sample_seconds"]), cfg["cpu_threshold"])
        measure("memory", memory_percent, cfg["memory_threshold"])
        for path in cfg["disk_paths"]:
            measure(f"disk:{path}", lambda p=path: disk_percent(p), cfg["disk_threshold"])
        for unit in cfg["services"]:
            measure(unit, lambda u=unit: check_service(u))
        measure("backup_age", lambda: backup_freshness(cfg))
        code = max({"ok": 0, "warning": 1, "error": 2}[item["status"]] for item in checks)
        report = {"time": timestamp(), "status": ("ok", "warning", "error")[code],
                  "exit_code": code, "checks": checks}
        atomic_json(cfg["state_dir"] / "status.json", report)
        event(cfg, report["status"], "monitor_completed", checks=checks, exit_code=code)
    return code


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def inspect_archive(archive, max_bytes):
    total, seen, members = 0, set(), []
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or not path.parts or path.parts[0] != "data"
                    or not (member.isfile() or member.isdir())
                    or str(path) in seen):
                raise LabError(f"Unsafe or duplicate archive member: {member.name}")
            if len(path.parts) == 1 and not member.isdir():
                raise LabError("Archive root must be a data directory")
            seen.add(str(path))
            total += member.size
            if total > max_bytes or len(seen) > 10000:
                raise LabError("Archive exceeds the configured restore size or file-count limit")
            members.append(member)
    if "data" not in seen:
        raise LabError("Archive has no data root")
    # A file must never become a parent directory while extracting.
    file_names = {PurePosixPath(m.name) for m in members if m.isfile()}
    if any(parent in file_names for m in members for parent in PurePosixPath(m.name).parents):
        raise LabError("Archive contains a file/directory path conflict")
    return members


def verify_archive(archive, max_bytes):
    archive = Path(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum) or digest(archive) != checksum:
        raise LabError(f"Checksum mismatch: {archive.name}")
    return inspect_archive(archive, max_bytes)


def backup(cfg):
    with lock(cfg, "backup"):
        source, directory = cfg["backup_source"], cfg["backup_dir"]
        if not source.is_dir():
            raise LabError(f"Backup source is missing: {source}")
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        paths = [source] + sorted(source.rglob("*"))
        total = 0
        for path in paths:
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise LabError(f"Backup accepts only regular files/directories: {path}")
            if stat.S_ISREG(mode):
                total += path.stat().st_size
        if total > cfg["max_backup_bytes"] or len(paths) > 10000:
            raise LabError("Backup source exceeds the configured size or file-count limit")
        if shutil.disk_usage(directory).free < total + 1024 * 1024:
            raise LabError("Insufficient free space for a new backup")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive = directory / f"backup-{stamp}-{uuid.uuid4().hex[:8]}.tar.gz"
        fd, name = tempfile.mkstemp(prefix=".pending-", dir=directory)
        os.close(fd)
        pending = Path(name)
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        try:
            with tarfile.open(pending, "w:gz", dereference=True) as handle:
                for path in paths:
                    relative = path.relative_to(source)
                    member_name = "data" if relative == Path(".") else f"data/{relative.as_posix()}"
                    # Source is trusted lab data; refuse links/special files again at write time.
                    def only_regular(member):
                        if not (member.isfile() or member.isdir()) or path.is_symlink():
                            raise LabError(f"Unsupported source entry: {path}")
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        return member
                    handle.add(path, arcname=member_name, recursive=False, filter=only_regular)
            inspect_archive(pending, cfg["max_backup_bytes"])
            with pending.open("rb") as handle:
                os.fsync(handle.fileno())
            sha = digest(pending)
            # Publish checksum first: only a final .tar.gz name is a completed backup.
            checksum.write_text(sha + "\n")
            checksum.chmod(0o640)
            pending.chmod(0o640)
            os.replace(pending, archive)
            verify_archive(archive, cfg["max_backup_bytes"])
            atomic_json(cfg["state_dir"] / "last_backup.json",
                        {"time": timestamp(), "archive": str(archive), "sha256": sha,
                         "source_bytes": total})
            event(cfg, "ok", "backup_created", archive=archive.name, source_bytes=total, sha256=sha)
            valid = []
            for old in backups(cfg):
                try:
                    verify_archive(old, cfg["max_backup_bytes"])
                    valid.append(old)
                except (OSError, LabError, tarfile.TarError, EOFError) as exc:
                    event(cfg, "warning", "retention_skipped_invalid_backup",
                          archive=old.name, detail=str(exc))
            for old in valid[cfg["keep_backups"]:]:
                old.unlink()
                old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)
                event(cfg, "ok", "backup_pruned", archive=old.name)
            return archive
        finally:
            pending.unlink(missing_ok=True)
            if not archive.exists():
                checksum.unlink(missing_ok=True)


def restore(cfg, archive, target):
    target = Path(target).absolute()
    if target.exists() or target.is_symlink():
        raise LabError("Restore destination must not exist; existing data will not be overwritten")
    if not target.parent.is_dir():
        raise LabError("Restore parent directory must already exist")
    with lock(cfg, "backup"):
        verify_archive(archive, cfg["max_backup_bytes"])
        # Extraction is manual: no links, ownership changes, devices, or archive modes.
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=target.parent) as temp:
            root = Path(temp) / "result"
            root.mkdir(mode=0o700)
            with tarfile.open(archive, "r:gz") as handle:
                for member in inspect_archive(archive, cfg["max_backup_bytes"]):
                    relative = PurePosixPath(member.name).relative_to("data")
                    destination = root.joinpath(*relative.parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        with handle.extractfile(member) as src, destination.open("xb") as dst:
                            shutil.copyfileobj(src, dst)
                        destination.chmod(0o600)
            # All validation and extraction must succeed before destination becomes visible.
            if target.exists() or target.is_symlink():
                raise LabError("Restore destination appeared during extraction")
            os.rename(root, target)
        event(cfg, "ok", "backup_restored", archive=Path(archive).name, destination=str(target))


def main(argv=None):
    os.umask(0o027)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/linux-admin/config.json")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("monitor", "backup", "report", "validate-config"):
        commands.add_parser(name)
    verify = commands.add_parser("verify-backup")
    verify.add_argument("archive")
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("archive")
    restore_parser.add_argument("destination")
    alert = commands.add_parser("alert")
    alert.add_argument("unit")
    args = parser.parse_args(argv)
    cfg = None
    try:
        cfg = load_config(args.config)
        if args.command == "validate-config":
            print("Configuration is valid")
        elif args.command == "monitor":
            return monitor(cfg)
        elif args.command == "backup":
            print(backup(cfg))
        elif args.command == "verify-backup":
            members = verify_archive(args.archive, cfg["max_backup_bytes"])
            event(cfg, "ok", "backup_verified", archive=Path(args.archive).name, entries=len(members))
        elif args.command == "restore":
            restore(cfg, args.archive, args.destination)
        elif args.command == "alert":
            event(cfg, "error", "systemd_job_failed", unit=args.unit,
                  next_step=f"journalctl -u {args.unit}.service --since '-10 minutes'")
        else:
            print((cfg["state_dir"] / "status.json").read_text(), end="")
        return 0
    except (OSError, ValueError, KeyError, LabError, tarfile.TarError, EOFError) as exc:
        code = 3 if isinstance(exc, BusyError) else 2
        print(f"ERROR: {exc}", file=sys.stderr)
        if cfg is not None:
            try:
                event(cfg, "error", "operation_failed", operation=args.command, detail=str(exc))
            except OSError:
                pass  # Original failure is still on stderr/journald, never changed to success.
        return code


if __name__ == "__main__":
    sys.exit(main())
