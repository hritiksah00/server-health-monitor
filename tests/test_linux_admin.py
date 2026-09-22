import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

import linux_admin as lab


ROOT = Path(__file__).resolve().parents[1]


class LabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = json.loads((ROOT / "config/local.json").read_text())
        for key in ("log_dir", "state_dir", "backup_dir", "backup_source"):
            self.raw[key] = str(self.root / key)
        self.raw["sample_seconds"] = 0.05
        self.file = self.root / "config.json"
        self.write_config()
        self.cfg = lab.load_config(self.file)
        self.cfg["backup_source"].mkdir()
        (self.cfg["backup_source"] / "notes.txt").write_text("recover this exact data\n")
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def write_config(self):
        self.file.write_text(json.dumps(self.raw))

    def run_monitor(self, cpu=10, memory=20, disk=30, service=None):
        with patch.object(lab, "cpu_percent", return_value=cpu), \
                patch.object(lab, "memory_percent", return_value=memory), \
                patch.object(lab, "disk_percent", return_value=disk):
            if service is not None:
                with patch.object(lab, "check_service", side_effect=service):
                    code = lab.monitor(self.cfg)
            else:
                code = lab.monitor(self.cfg)
        return code, json.loads((self.cfg["state_dir"] / "status.json").read_text())

    def test_invalid_thresholds_are_rejected(self):
        for value in (-1, 101, True, "80", float("nan")):
            with self.subTest(value=value):
                self.raw["cpu_threshold"] = value
                self.write_config()
                with self.assertRaises(lab.LabError):
                    lab.load_config(self.file)

    def test_unknown_config_key_is_not_silently_ignored(self):
        self.raw["cpu_thresold"] = 80
        self.write_config()
        with self.assertRaises(lab.LabError):
            lab.load_config(self.file)

    def test_backup_destination_cannot_be_inside_source(self):
        self.raw["backup_dir"] = str(self.cfg["backup_source"] / "recursive-backups")
        self.write_config()
        with self.assertRaises(lab.LabError):
            lab.load_config(self.file)

    def test_invalid_config_exits_two(self):
        self.file.write_text("{invalid")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lab.main(["--config", str(self.file), "monitor"]), 2)

    def test_cpu_uses_counter_delta_and_excludes_idle(self):
        with patch.object(lab, "read_cpu", side_effect=[(1000, 400), (1100, 425)]), \
                patch.object(lab.time, "sleep"):
            self.assertEqual(lab.cpu_percent(1), 75)

    def test_nonadvancing_cpu_counters_are_an_error(self):
        with patch.object(lab, "read_cpu", return_value=(1000, 400)), patch.object(lab.time, "sleep"):
            with self.assertRaises(lab.LabError):
                lab.cpu_percent(1)

    def test_memory_counts_available_including_reclaimable_cache(self):
        with patch.object(Path, "read_text", return_value="MemTotal: 1000 kB\nMemFree: 50 kB\nMemAvailable: 400 kB\n"):
            self.assertEqual(lab.memory_percent(), 60)

    def test_healthy_report_and_json_log(self):
        lab.backup(self.cfg)
        code, report = self.run_monitor()
        self.assertEqual((code, report["status"]), (0, "ok"))
        records = (self.cfg["log_dir"] / "operations.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(records[-1])["exit_code"], 0)

    def test_threshold_boundary_is_warning(self):
        lab.backup(self.cfg)
        code, report = self.run_monitor(cpu=self.cfg["cpu_threshold"])
        self.assertEqual(code, 1)
        self.assertEqual(report["checks"][0]["status"], "warning")

    def test_unavailable_measurement_cannot_be_healthy(self):
        lab.backup(self.cfg)
        with patch.object(lab, "cpu_percent", side_effect=OSError("proc unavailable")):
            self.assertEqual(lab.monitor(self.cfg), 2)
        report = json.loads((self.cfg["state_dir"] / "status.json").read_text())
        self.assertEqual(report["checks"][0]["status"], "error")

    def test_service_query_failure_is_error(self):
        lab.backup(self.cfg)
        self.cfg["services"] = ["ssh.service"]
        code, report = self.run_monitor(service=FileNotFoundError("systemctl missing"))
        self.assertEqual(code, 2)
        self.assertIn("systemctl missing", str(report))

    def test_missing_or_inactive_service_is_warning(self):
        for load_state, active in (("not-found", "inactive"), ("loaded", "failed")):
            result = subprocess.CompletedProcess([], 0,
                f"LoadState={load_state}\nActiveState={active}\nSubState=dead\n", "")
            with patch.object(lab.subprocess, "run", return_value=result):
                self.assertEqual(lab.check_service("test.service")["status"], "warning")

    def test_service_query_timeout_is_error(self):
        lab.backup(self.cfg)
        self.cfg["services"] = ["ssh.service"]
        code, _ = self.run_monitor(service=subprocess.TimeoutExpired("systemctl", 5))
        self.assertEqual(code, 2)

    def test_missing_and_stale_backups_raise_warnings(self):
        code, _ = self.run_monitor()
        self.assertEqual(code, 1)
        archive = lab.backup(self.cfg)
        old = time.time() - 48 * 3600
        os.utime(archive, (old, old))
        code, report = self.run_monitor()
        self.assertEqual(code, 1)
        self.assertEqual(report["checks"][-1]["status"], "warning")

    def test_logging_failure_returns_nonzero(self):
        with patch.object(lab, "event", side_effect=PermissionError("log unwritable")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lab.main(["--config", str(self.file), "monitor"]), 2)

    def test_overlap_returns_distinct_exit_code(self):
        with lab.lock(self.cfg, "monitor"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lab.main(["--config", str(self.file), "monitor"]), 3)

    def test_backup_round_trip_preserves_file_contents(self):
        nested = self.cfg["backup_source"] / "nested directory"
        nested.mkdir()
        (nested / "bytes.bin").write_bytes(bytes(range(256)))
        archive = lab.backup(self.cfg)
        restored = self.root / "restored"
        lab.restore(self.cfg, archive, restored)
        self.assertEqual((restored / "notes.txt").read_text(), "recover this exact data\n")
        self.assertEqual((restored / "nested directory/bytes.bin").read_bytes(), bytes(range(256)))
        self.assertEqual((restored / "notes.txt").stat().st_mode & 0o777, 0o600)

    def test_restore_refuses_existing_destination(self):
        archive = lab.backup(self.cfg)
        with self.assertRaises(lab.LabError):
            lab.restore(self.cfg, archive, self.cfg["backup_source"])

    def test_corruption_is_detected_before_restore(self):
        archive = lab.backup(self.cfg)
        with archive.open("ab") as handle:
            handle.write(b"tampered")
        target = self.root / "restore"
        with self.assertRaisesRegex(lab.LabError, "Checksum mismatch"):
            lab.restore(self.cfg, archive, target)
        self.assertFalse(target.exists())

    def test_failed_backup_preserves_existing_archive(self):
        archive = lab.backup(self.cfg)
        (self.cfg["backup_source"] / "outside-link").symlink_to("/etc/passwd")
        with self.assertRaises(lab.LabError):
            lab.backup(self.cfg)
        self.assertTrue(archive.exists())
        self.assertEqual(len(lab.backups(self.cfg)), 1)
        self.assertFalse(list(self.cfg["backup_dir"].glob(".pending-*")))

    def test_retention_keeps_newest_valid_backups_only(self):
        self.cfg["keep_backups"] = 2
        created = [lab.backup(self.cfg) for _ in range(4)]
        self.assertEqual(lab.backups(self.cfg), list(reversed(created[-2:])))
        self.assertFalse(created[0].with_suffix(".gz.sha256").exists())

    def test_corrupt_old_backup_does_not_displace_a_valid_backup(self):
        self.cfg["keep_backups"] = 2
        old = lab.backup(self.cfg)
        old.with_suffix(".gz.sha256").write_text("0" * 64)
        valid = [lab.backup(self.cfg) for _ in range(2)]
        self.assertTrue(all(path.exists() for path in valid))
        self.assertTrue(old.exists())
        self.assertIn("retention_skipped_invalid_backup", self.output.getvalue())

    def test_source_size_limit_prevents_partial_backup(self):
        self.cfg["max_backup_bytes"] = 1
        with self.assertRaises(lab.LabError):
            lab.backup(self.cfg)
        self.assertEqual(lab.backups(self.cfg), [])

    def test_disk_full_does_not_remove_old_backup(self):
        old = lab.backup(self.cfg)
        with patch.object(lab.shutil, "disk_usage", return_value=type("Usage", (), {"free": 0})()):
            with self.assertRaisesRegex(lab.LabError, "Insufficient"):
                lab.backup(self.cfg)
        self.assertTrue(old.exists())

    def malicious_archive(self, name, kind=tarfile.REGTYPE):
        archive = self.root / "unsafe.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            root = tarfile.TarInfo("data")
            root.type = tarfile.DIRTYPE
            handle.addfile(root)
            item = tarfile.TarInfo(name)
            item.type = kind
            if kind == tarfile.SYMTYPE:
                item.linkname = "/etc"
            handle.addfile(item)
        archive.with_suffix(".gz.sha256").write_text(lab.digest(archive))
        return archive

    def test_restore_rejects_traversal_absolute_and_link_members(self):
        for name, kind in (("data/../../escape", tarfile.REGTYPE),
                           ("/escape", tarfile.REGTYPE), ("data/link", tarfile.SYMTYPE)):
            with self.subTest(name=name):
                archive = self.malicious_archive(name, kind)
                with self.assertRaises(lab.LabError):
                    lab.restore(self.cfg, archive, self.root / "restore")
                self.assertFalse((self.root / "restore").exists())

    def test_log_rotation_reopens_new_file(self):
        lab.event(self.cfg, "ok", "before_rotation")
        path = self.cfg["log_dir"] / "operations.jsonl"
        rotated = path.with_suffix(".jsonl.1")
        path.rename(rotated)
        lab.event(self.cfg, "ok", "after_rotation")
        self.assertIn("before_rotation", rotated.read_text())
        self.assertNotIn("before_rotation", path.read_text())
        self.assertIn("after_rotation", path.read_text())


class FirewallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / "calls"
        executable = self.root / "ufw"
        executable.write_text(
            "#!/usr/bin/env python3\nimport json,os,sys\n"
            "with open(os.environ['UFW_CALLS'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "if sys.argv[1:]==['status']: print('Status: inactive')\n")
        executable.chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                    "UFW_CALLS": str(self.calls)}
        self.env.pop("SSH_CONNECTION", None)

    def invoke(self, *args):
        return subprocess.run(["bash", str(ROOT / "scripts/firewall.sh"), *args],
                              env=self.env, capture_output=True, text=True, check=False)

    def test_preview_never_enables_firewall(self):
        result = self.invoke("--admin-cidr", "192.0.2.10/32")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertTrue(all(args[0] == "--dry-run" for args in calls))
        self.assertEqual(calls[0][1], "allow")
        self.assertFalse(any("enable" in args for args in calls))

    def test_public_network_and_invalid_port_are_rejected(self):
        for args in (("--admin-cidr", "0.0.0.0/0"), ("--admin-cidr", "invalid"),
                     ("--admin-cidr", "192.0.2.10/32", "--ssh-port", "70000")):
            self.assertNotEqual(self.invoke(*args).returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_current_ssh_client_must_be_in_allowed_network(self):
        self.env["SSH_CONNECTION"] = "198.51.100.5 3456 192.0.2.1 22"
        self.assertNotEqual(self.invoke("--admin-cidr", "192.0.2.10/32").returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_apply_requires_explicit_console_confirmation(self):
        self.assertNotEqual(self.invoke("--admin-cidr", "192.0.2.10/32", "--apply").returncode, 0)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
