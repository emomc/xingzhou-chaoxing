"""Create private Linux deployment configuration without overwriting existing files."""

import argparse
import configparser
import json
import os
from pathlib import Path
import secrets


def initialize(directory, port=3000):
    directory = Path(directory)
    targets = [
        directory / ".env",
        directory / "runtime/webai/config.yaml",
        directory / "runtime/course/config.ini",
    ]
    if any(path.exists() or path.is_symlink() for path in targets):
        raise FileExistsError("Deployment config already exists; it was not overwritten")
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    token = "sk-local-" + secrets.token_hex(32)
    bridge = {
        "logLevel": "warn",
        "server": {
            "port": 3000,
            "auth": token,
            "keepalive": {"mode": "comment"},
            "imageMarkdown": False,
        },
        "backend": {
            "pool": {
                "strategy": "least_busy",
                "failover": {"enabled": False, "maxRetries": 1},
                "waitTimeout": 180000,
                "instances": [{
                    "name": "study_browser",
                    "userDataMark": "study",
                    "workers": [{"name": "deepseek", "type": "deepseek_text"}],
                }],
            },
        },
        "queue": {"queueBuffer": 1, "imageLimit": 10},
        "browser": {"headless": False, "path": "", "proxy": {"enable": False}},
    }
    config = configparser.ConfigParser(interpolation=None)
    config["common"] = {
        "use_cookies": "false",
        "username": "",
        "password": "",
        "course_list": "",
        "speed": "1",
        "jobs": "1",
        "notopen_action": "continue",
    }
    config["tiku"] = {
        "provider": "WebAI",
        "submit": "false",
        "cover_rate": "1.0",
        "delay": "1",
        "true_list": "\u6b63\u786e,\u5bf9,\u662f",
        "false_list": "\u9519\u8bef,\u9519,\u5426",
        "webai_endpoint": "http://webai:3000/v1",
        "webai_key_env": "WEBAI_API_KEY",
        "webai_model": "deepseek-search",
        "webai_stream": "true",
        "webai_timeout": "240",
        "webai_cache": "/state/webai-cache.json",
        "webai_vision_model": "auto",
        "webai_vision_endpoint": "",
        "webai_vision_key_env": "",
        "webai_image_hosts": "",
        "webai_preview": "true",
        "min_interval_seconds": "3",
        "http_proxy": "",
    }
    config["notification"] = {"provider": ""}
    for path in [directory, directory / "runtime", targets[1].parent, targets[2].parent]:
        if path.is_symlink():
            raise ValueError("Deployment directories cannot be symbolic links")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    env_content = f"WEBAI_API_KEY={token}\nWEBAI_PORT={port}\nTZ=Asia/Shanghai\n"
    for target, content in [
        (targets[0], env_content),
        (targets[1], json.dumps(bridge, ensure_ascii=False, indent=2) + "\n"),
    ]:
        # JSON is valid YAML; use the standard serializer instead of templating a secret into YAML.
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    fd = os.open(targets[2], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        config.write(stream)
    return targets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args(argv)
    try:
        targets = initialize(args.directory, args.port)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Setup failed: {exc}\n")
    for target in targets:
        print(f"Created {target}")
    print("Local token is in .env; keep it private. AI login must be completed manually in WebUI.")
    print("Preview mode is enabled. No course answers will be saved or submitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
