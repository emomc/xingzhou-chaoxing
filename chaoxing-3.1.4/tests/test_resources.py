from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.resources import (
    ResourceMonitor, cpu_ticks, disk_sample, memory_sample, mount_table,
    network_counters, path_mount,
)


MEMORY = """MemTotal: 1000 kB
MemAvailable: 200 kB
SwapTotal: 500 kB
SwapFree: 300 kB
"""

MOUNTS = """24 1 254:1 / / rw,relatime - ext4 /dev/vda1 rw
35 24 254:17 / /data rw,relatime - ext4 /dev/vdb1 rw
40 24 254:17 /chaoxing/backend /var/lib/chaoxing-backend rw - ext4 /dev/vdb1 rw
41 24 254:17 /chaoxing/webai /var/lib/webai-bridge rw - ext4 /dev/vdb1 rw
42 24 0:23 / /proc rw - proc proc rw
"""


def network(received=1000, sent=2000):
    return (
        "Inter-| Receive | Transmit\n face |bytes\n"
        f" eth0: {received} 0 0 0 0 0 0 0 {sent} 0 0 0 0 0 0 0\n"
        " lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n"
    )


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        sysconf = patch("backend.resources.os.sysconf", return_value=4096, create=True)
        sysconf.start()
        self.addCleanup(sysconf.stop)
        (self.root / "net").mkdir()
        (self.root / "self").mkdir()
        self.now = 0
        for name, text in (
            ("stat", "cpu 100 0 50 850 0 0 0 0 20 30\n"),
            ("meminfo", MEMORY), ("net/dev", network()),
            ("loadavg", "0.10 0.20 0.30 1/40 100\n"),
            ("uptime", "90061.99 4\n"), ("self/statm", "100 10 3\n"),
        ):
            (self.root / name).write_text(text, encoding="ascii")
        self.monitor = ResourceMonitor(
            self.root, proc_root=self.root, clock=lambda: self.now, wall_clock=lambda: 1234,
        )

    def test_cpu_guest_counters_are_not_counted_twice(self):
        self.assertEqual(cpu_ticks("cpu 100 20 30 800 50 0 0 0 9000 9000\n"), (1000, 850))
        for text in ("", "cpu0 1 2 3 4", "cpu 1 -2 3 4"):
            with self.subTest(text=text), self.assertRaises((ValueError, IndexError)):
                cpu_ticks(text)

    def test_memory_swap_and_fallback_are_bounded(self):
        memory, swap = memory_sample(MEMORY)
        self.assertEqual(memory["used_bytes"], 800 * 1024)
        self.assertEqual(memory["percent"], 80)
        self.assertEqual(swap["percent"], 40)
        fallback, disabled = memory_sample(
            "MemTotal: 100 kB\nMemFree: 10 kB\nCached: 20 kB\n"
            "Buffers: 5 kB\nSReclaimable: 15 kB\nShmem: 10 kB\n"
        )
        self.assertEqual(fallback["available_bytes"], 40 * 1024)
        self.assertFalse(disabled["enabled"])
        bounded, _ = memory_sample("MemTotal: 100 kB\nMemAvailable: 200 kB\n")
        self.assertEqual(bounded["percent"], 0)

    def test_snapshot_is_partial_on_missing_metrics_without_fake_zeroes(self):
        (self.root / "meminfo").unlink()
        (self.root / "uptime").unlink()
        sample = self.monitor.snapshot()
        self.assertEqual(sample["status"], "partial")
        self.assertIsNone(sample["memory"])
        self.assertIsNone(sample["swap"])
        self.assertIsNone(sample["uptime_seconds"])
        self.assertIn("memory", sample["unavailable"])
        self.assertIsNotNone(sample["disk"])
        self.assertEqual(sample["scope"], "backend_host")

    def test_cpu_network_rates_and_uptime_use_counter_differences(self):
        first = self.monitor.snapshot()
        self.assertIsNone(first["cpu"]["percent"])
        self.assertIsNone(first["network"]["received_bytes_per_second"])
        (self.root / "stat").write_text("cpu 150 0 100 950 0 0 0 0 9999 9999\n")
        (self.root / "net/dev").write_text(network(1300, 2600))
        self.now = 3
        second = self.monitor.snapshot()
        self.assertEqual(second["cpu"]["percent"], 50)
        self.assertEqual(second["network"]["received_bytes_per_second"], 100)
        self.assertEqual(second["network"]["sent_bytes_per_second"], 200)
        self.assertEqual(second["uptime_seconds"], 90061)
        self.assertEqual(second["sampled_at"], 1234)
        self.assertEqual(network_counters(network()), (1000, 2000))

    def test_cache_is_two_seconds_and_does_not_share_mutable_results(self):
        first = self.monitor.snapshot()
        first["cpu"]["percent"] = 99
        (self.root / "meminfo").unlink()
        self.now = 1.99
        cached = self.monitor.snapshot()
        self.assertEqual(cached["status"], "ok")
        self.assertIsNone(cached["cpu"]["percent"])
        self.now = 2
        self.assertEqual(self.monitor.snapshot()["status"], "partial")

    def test_reset_counters_do_not_create_negative_percent_or_rates(self):
        self.monitor.snapshot()
        (self.root / "stat").write_text("cpu 1 0 1 10 0 0 0 0\n")
        (self.root / "net/dev").write_text(network(1, 1))
        self.now = 3
        result = self.monitor.snapshot()
        self.assertIsNone(result["cpu"]["percent"])
        self.assertIsNone(result["network"]["sent_bytes_per_second"])
        (self.root / "stat").write_text("cpu 6 0 6 20 0 0 0 0\n")
        self.now = 6
        self.assertEqual(self.monitor.snapshot()["cpu"]["percent"], 50)

    def test_unsupported_platform_reports_only_available_metrics(self):
        with patch("backend.resources.sys.platform", "win32"):
            monitor = ResourceMonitor(self.root)
            result = monitor.snapshot()
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["memory"])
        self.assertIsNone(result["cpu"]["percent"])
        self.assertIsNotNone(result["disk"])
        self.assertIn("unsupported_host_metrics", result["unavailable"])

    def test_invalid_load_and_nonfinite_uptime_are_not_returned(self):
        (self.root / "loadavg").write_text("nan 0.2 0.3\n")
        (self.root / "uptime").write_text("inf 2\n")
        sample = self.monitor.snapshot()
        self.assertIsNone(sample["cpu"]["load_average"])
        self.assertIsNone(sample["uptime_seconds"])
        self.assertIn("load", sample["unavailable"])

    def test_mountinfo_decodes_paths_and_ignores_invalid_records(self):
        mounts = mount_table(
            MOUNTS + r"50 24 254:19 / /other\040data rw - ext4 /dev/mapper/data\134disk rw"
            "\ninvalid\n1 2 invalid / /bad rw - ext4 /dev/bad rw\n"
        )
        self.assertEqual(len(mounts), 6)
        self.assertEqual(mounts[-1]["mountpoint"], "/other data")
        self.assertEqual(mounts[-1]["device"], "/dev/mapper/data\\disk")
        self.assertEqual(mounts[2]["root"], "/chaoxing/backend")

    def test_mount_matching_respects_path_boundaries_and_nested_mounts(self):
        mounts = mount_table(MOUNTS)
        self.assertEqual(path_mount("/var/lib/chaoxing-backend/service.sqlite3", mounts)["root"],
                         "/chaoxing/backend")
        self.assertEqual(path_mount("/var/lib/chaoxing-backend-other", mounts)["device"],
                         "/dev/vda1")
        self.assertEqual(path_mount("/database", mounts)["device"], "/dev/vda1")
        self.assertIsNone(path_mount("/unavailable", []))

    def test_bind_mount_sample_identifies_the_real_data_disk_and_source_directory(self):
        with patch("backend.resources.shutil.disk_usage",
                   return_value=SimpleNamespace(total=1000, used=200, free=800)):
            data = disk_sample("/var/lib/chaoxing-backend", mount_table(MOUNTS), "data")
            nested = disk_sample("/var/lib/chaoxing-backend/cache", mount_table(MOUNTS), "data")
            system = disk_sample("/", mount_table(MOUNTS), "system")
        self.assertEqual(data["device"], "/dev/vdb1")
        self.assertEqual(data["mountpoint"], "/data")
        self.assertEqual(data["path"], "/data/chaoxing/backend")
        self.assertEqual(nested["path"], "/data/chaoxing/backend/cache")
        self.assertEqual(data["percent"], 20)
        self.assertEqual(system["device"], "/dev/vda1")
        self.assertEqual(system["mountpoint"], "/")
        self.assertEqual(data["scope"], "backend_data_volume")

    def test_separate_system_and_data_volumes_are_reported_without_bind_duplicates(self):
        system = {"id": "system", "role": "system", "device_id": "254:1", "path": "/"}
        data = {"id": "data", "role": "data", "device_id": "254:17",
                "path": "/data/chaoxing/backend"}
        with patch("backend.resources.disk_sample", side_effect=[system, data]):
            sample = self.monitor.snapshot()
        self.assertFalse(sample["data_disk_shared"])
        self.assertEqual([disk["id"] for disk in sample["disks"]], ["system", "data"])
        self.assertEqual(sample["data_directory"], "/data/chaoxing/backend")
        self.assertFalse(sample["disks"][0]["contains_state"])
        self.assertTrue(sample["disks"][1]["contains_state"])
        self.assertEqual(sample["disk"]["id"], "data")

    def test_shared_volume_is_not_falsely_advertised_as_a_separate_data_disk(self):
        system = {"id": "system", "role": "system", "device_id": "254:1", "path": "/"}
        data = {"id": "data", "role": "data", "device_id": "254:1",
                "path": "/var/lib/chaoxing-backend"}
        with patch("backend.resources.disk_sample", side_effect=[system, data]):
            sample = self.monitor.snapshot()
        self.assertTrue(sample["data_disk_shared"])
        self.assertEqual(len(sample["disks"]), 1)
        self.assertTrue(sample["disks"][0]["contains_state"])
        self.assertEqual(sample["data_directory"], "/var/lib/chaoxing-backend")
        self.assertIsNotNone(sample["disk"])

    def test_disk_failure_does_not_hide_the_other_volume(self):
        data = {"id": "data", "role": "data", "device_id": "254:17",
                "path": "/data/chaoxing/backend"}
        with patch("backend.resources.disk_sample", side_effect=[OSError("unavailable"), data]):
            sample = self.monitor.snapshot()
        self.assertEqual(sample["status"], "partial")
        self.assertEqual(sample["disks"], [data])
        self.assertIsNone(sample["data_disk_shared"])
        self.assertIn("system_disk", sample["unavailable"])
        self.assertEqual(sample["disk"], data)


if __name__ == "__main__":
    unittest.main()
