"""Small, cached host samples without subprocesses or recursive disk scans."""

import copy
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import threading
import time


def ratio(used, total):
    return round(max(0, min(100, 100 * used / total)), 1) if total > 0 else 0


def cpu_ticks(text):
    fields = text.splitlines()[0].split()
    if fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("Invalid aggregate CPU counters")
    ticks = [int(value) for value in fields[1:9]]
    if any(value < 0 for value in ticks):
        raise ValueError("Invalid CPU counter")
    # Guest counters are already included in user/nice, so only the first eight count.
    return sum(ticks), ticks[3] + (ticks[4] if len(ticks) > 4 else 0)


def memory_sample(text):
    values = {}
    for line in text.splitlines():
        name, _, raw = line.partition(":")
        fields = raw.split()
        if fields and fields[0].isdigit():
            values[name] = int(fields[0]) * (1024 if fields[1:] == ["kB"] else 1)
    total = values["MemTotal"]
    if total <= 0:
        raise ValueError("Invalid memory size")
    available = values.get("MemAvailable")
    if available is None:
        available = sum(values.get(name, 0) for name in
                        ("MemFree", "Buffers", "Cached", "SReclaimable")) - values.get("Shmem", 0)
    available = max(0, min(total, available))
    swap_total = values.get("SwapTotal", 0)
    swap_free = max(0, min(swap_total, values.get("SwapFree", 0)))
    return (
        {"total_bytes": total, "used_bytes": total - available,
         "available_bytes": available, "percent": ratio(total - available, total)},
        {"total_bytes": swap_total, "used_bytes": swap_total - swap_free,
         "free_bytes": swap_free, "percent": ratio(swap_total - swap_free, swap_total),
         "enabled": swap_total > 0},
    )


def network_counters(text):
    received = sent = 0
    for line in text.splitlines()[2:]:
        name, separator, raw = line.partition(":")
        fields = raw.split()
        if not separator or name.strip() == "lo" or len(fields) < 16:
            continue
        received += int(fields[0])
        sent += int(fields[8])
    return received, sent


def mount_table(text):
    mounts = []
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        fields, filesystem = left.split(), right.split()
        if not separator or len(fields) < 6 or len(filesystem) < 3 \
                or not re.fullmatch(r"\d+:\d+", fields[2]):
            continue
        unescape = lambda value: re.sub(
            r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), value,
        )
        root, point = unescape(fields[3]), unescape(fields[4])
        if not root.startswith("/") or not point.startswith("/"):
            continue
        mounts.append({
            "device_id": fields[2], "root": root, "mountpoint": point,
            "filesystem": filesystem[0], "device": unescape(filesystem[1]),
        })
    return mounts


def path_mount(path, mounts):
    path = PurePosixPath(path)
    candidates = [mount for mount in mounts if path.is_relative_to(mount["mountpoint"])]
    return max(candidates, key=lambda mount: len(mount["mountpoint"]), default=None)


def disk_sample(path, mounts, role):
    usage = shutil.disk_usage(path)
    mount = path_mount(str(path), mounts)
    volume = mount
    physical_path = str(path)
    if mount is not None:
        # Bind mounts retain the source filesystem root in mountinfo.
        roots = [item for item in mounts
                 if item["device_id"] == mount["device_id"] and item["root"] == "/"]
        volume = min(roots, key=lambda item: len(item["mountpoint"]), default=mount)
        if volume["root"] == "/":
            relative = PurePosixPath(str(path)).relative_to(mount["mountpoint"])
            physical_path = str(
                PurePosixPath(volume["mountpoint"]) / mount["root"].lstrip("/") / relative
            )
    return {
        "id": role, "role": role,
        "scope": "backend_data_volume" if role == "data" else "system_volume",
        "device_id": mount["device_id"] if mount else str(os.stat(path).st_dev),
        "device": volume["device"] if volume else None,
        "filesystem": volume["filesystem"] if volume else None,
        "mountpoint": volume["mountpoint"] if volume else None,
        "path": physical_path,
        "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
        "percent": ratio(usage.used, usage.total),
    }


class ResourceMonitor:
    def __init__(self, state_dir, *, proc_root=None, clock=time.monotonic, wall_clock=time.time):
        self.state_dir = Path(state_dir)
        self.system_dir = Path(self.state_dir.resolve().anchor)
        self.proc_root = Path(proc_root) if proc_root is not None else Path("/proc")
        self.supported = proc_root is not None or sys.platform.startswith("linux")
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.Lock()
        self.cached = None
        self.sampled_at = None
        self.previous_cpu = None
        self.previous_network = None
        self.previous_network_at = None
        if self.supported:
            try:
                self.previous_cpu = cpu_ticks(self._read("stat"))
            except (OSError, ValueError, IndexError):
                pass

    def _read(self, name):
        return (self.proc_root / name).read_text(encoding="ascii")

    def snapshot(self):
        with self.lock:
            now = self.clock()
            if self.cached is not None and 0 <= now - self.sampled_at < 2:
                return copy.deepcopy(self.cached)
            result = {
                "scope": "backend_host", "platform": sys.platform,
                "sampled_at": self.wall_clock(), "status": "ok" if self.supported else "partial",
                "cpu": {"percent": None, "logical_count": os.cpu_count(), "load_average": None},
                "memory": None, "swap": None, "disk": None, "disks": [], "network": None,
                "data_disk_shared": None, "data_directory": None,
                "uptime_seconds": None, "backend_rss_bytes": None,
            }
            errors = []
            mounts = []
            if self.supported:
                try:
                    mounts = mount_table(self._read("self/mountinfo"))
                except (OSError, UnicodeError):
                    pass
            system = data = None
            try:
                system = disk_sample(self.system_dir, mounts, "system")
                system["contains_state"] = False
                result["disks"].append(system)
            except OSError:
                errors.append("system_disk")
            try:
                data = disk_sample(self.state_dir.resolve(), mounts, "data")
                data["contains_state"] = True
                result["disk"] = data
                result["data_directory"] = data["path"]
            except OSError:
                errors.append("data_disk")
            if system and data:
                shared = system["device_id"] == data["device_id"]
                result["data_disk_shared"] = shared
                if shared:
                    system["contains_state"] = True
                else:
                    result["disks"].append(data)
            elif data:
                result["disks"].append(data)
            if self.supported:
                try:
                    current = cpu_ticks(self._read("stat"))
                    if self.previous_cpu is not None:
                        total = current[0] - self.previous_cpu[0]
                        idle = current[1] - self.previous_cpu[1]
                        if total > 0 and 0 <= idle <= total:
                            result["cpu"]["percent"] = ratio(total - idle, total)
                    self.previous_cpu = current
                except (OSError, ValueError, IndexError):
                    self.previous_cpu = None
                    errors.append("cpu")
                try:
                    result["memory"], result["swap"] = memory_sample(self._read("meminfo"))
                except (OSError, ValueError, KeyError):
                    errors.append("memory")
                try:
                    loads = [float(value) for value in self._read("loadavg").split()[:3]]
                    if len(loads) != 3 or any(not math.isfinite(value) or value < 0 for value in loads):
                        raise ValueError("Invalid load average")
                    result["cpu"]["load_average"] = loads
                except (OSError, ValueError):
                    errors.append("load")
                try:
                    uptime = float(self._read("uptime").split()[0])
                    if not math.isfinite(uptime) or uptime < 0:
                        raise ValueError("Invalid uptime")
                    result["uptime_seconds"] = int(uptime)
                except (OSError, ValueError, IndexError):
                    errors.append("uptime")
                try:
                    current = network_counters(self._read("net/dev"))
                    network = {"received_bytes": current[0], "sent_bytes": current[1],
                               "received_bytes_per_second": None, "sent_bytes_per_second": None}
                    if self.previous_network is not None and now > self.previous_network_at:
                        elapsed = now - self.previous_network_at
                        for index, key in enumerate(("received_bytes_per_second", "sent_bytes_per_second")):
                            change = current[index] - self.previous_network[index]
                            if change >= 0:
                                network[key] = round(change / elapsed, 1)
                    self.previous_network, self.previous_network_at = current, now
                    result["network"] = network
                except (OSError, ValueError):
                    self.previous_network = self.previous_network_at = None
                    errors.append("network")
                try:
                    resident = int(self._read("self/statm").split()[1])
                    result["backend_rss_bytes"] = resident * os.sysconf("SC_PAGE_SIZE")
                except (OSError, ValueError, IndexError):
                    errors.append("process")
            else:
                errors.append("unsupported_host_metrics")
            if errors:
                result["status"] = "partial"
            result["unavailable"] = errors
            self.cached, self.sampled_at = result, now
            return copy.deepcopy(result)
