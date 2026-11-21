"""Offline-only UI integration harness; uses fake processes and fake AI."""

import argparse
from pathlib import Path
import signal
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waitress import create_server
from backend.service import Service, Settings, create_app
from test_backend import FakeAI, FakeProcess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=3310)
    parser.add_argument("--account-fixture", action="store_true")
    args = parser.parse_args()

    def course_process(_args, **kwargs):
        kwargs["env"]["OFFLINE_FIXTURE_HOLD"] = "0"
        fixture = Path(__file__).parent / "fixtures" / "course_stub.py"
        return subprocess.Popen([sys.executable, str(fixture)], **kwargs)

    service = Service(
        Settings(state_dir=args.state_dir, port=args.port),
        process_factory=FakeProcess, client_factory=FakeAI,
        course_process_factory=course_process,
    )
    if not service.store.accounts():
        for index in range(3):
            account = service.store.save_account(
                f"OFFLINE TEST {index + 1}", f"fake-{index}", "fake-password-only",
            )
            job, _ = service.store.create_job(account["id"], {
                "course_ids": ["1"], "submit": False, "preview": False, "videos_only": True,
            })
            service.store.event(job["id"], "chapter", {
                "course": "Offline integration test", "chapter": f"Test chapter {index + 1}",
                "completed": index, "total": 5,
            })
            if index == 1:
                service.store.action(job["id"], "pause")
            elif index == 2:
                service.store.set_state(job["id"], "needs_attention", error="INCOMPLETE_TASKS")
        account = service.store.save_account("OFFLINE COURSE PICKER", "fake-course-picker", "fake-password-only")
        if args.account_fixture:
            finished, _ = service.store.create_job(account["id"], {
                "course_ids": ["1", "2"], "submit": True, "preview": False, "videos_only": False,
            })
            for course_id in ("1", "2"):
                service.store.event(finished["id"], "course_result", {
                    "course_id": course_id, "course": "Offline completed course " + course_id,
                    "phase": "course_confirmed", "remaining_chapters": 0,
                })
            service.store.set_state(finished["id"], "succeeded")
            service.store.delete_job(finished["id"])
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(5):
            with service.scheduler.lock:
                ids = list(service.scheduler.workers)
            for job_id in ids:
                service.store.event(job_id, "heartbeat", {})

    service.start()
    threading.Thread(target=heartbeat, daemon=True).start()
    server = create_server(create_app(service), host="127.0.0.1", port=args.port, threads=8)

    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    print(f"OFFLINE MOCK API ONLY: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.close()
        service.close()


if __name__ == "__main__":
    main()
