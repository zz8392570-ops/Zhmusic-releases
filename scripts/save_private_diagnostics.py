#!/usr/bin/env python3
"""Save temporary build failure logs to a private draft release, never public artifacts."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TARGET = "zz8392570-ops/Zhmusic"


def run(*args):
    # Capture all output: diagnostics themselves and credentials must not appear
    # in the public coordinator's console, including on a failed gh command.
    result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode:
        raise RuntimeError("GitHub 未能保存私有诊断，请检查账号权限及网络。")
    return result.stdout


def save(root: Path, run_id: str, attempt: str):
    if not re.fullmatch(r"[0-9]+", run_id) or not re.fullmatch(r"[0-9]+", attempt):
        raise ValueError("Invalid workflow run identity")
    if os.environ.get("SOURCE_REPOSITORY") != TARGET:
        raise ValueError("Refusing to save diagnostics outside the private source repository")
    logs = sorted(root.glob("*/failed-build.log"))
    # Never follow a symlink supplied by the checked-out source or a build.
    logs = [p for p in logs if p.is_file() and not p.is_symlink() and not p.parent.is_symlink()]
    if not logs:
        print("没有需要保存的临时构建失败日志。")
        return
    repo = json.loads(run("gh", "api", f"repos/{TARGET}"))
    if repo.get("full_name", "").lower() != TARGET.lower() or repo.get("private") is not True:
        raise RuntimeError("源码仓库不是私有状态，已停止保存诊断。")
    # A run normally creates one session. Bundle distinct file names when a
    # recovered invocation leaves more than one, without printing file contents.
    import tempfile
    import zipfile
    with tempfile.TemporaryDirectory(prefix="zhmusic-private-log-") as temp:
        archive = Path(temp) / "failed-build-diagnostics.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for log in logs:
                output.write(log, f"{log.parent.name}/failed-build.log")
                state = log.parent / "session.json"
                if state.is_file() and not state.is_symlink():
                    output.write(state, f"{log.parent.name}/session.json")
        tag = f"diagnostics-{run_id}-{attempt}"
        run("gh", "release", "create", tag, str(archive), "--repo", TARGET,
            "--draft", "--latest=false", "--target", "main", "--title", f"私有构建诊断 {run_id}.{attempt}",
            "--notes", "临时构建的失败日志与恢复记录。仅保存在本私有仓库。")
    print(f"失败诊断已保存至私有源码仓库的草稿 Release：{tag}")


if __name__ == "__main__":
    try:
        save(Path(sys.argv[1]), os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    except Exception as error:
        # Do not include untrusted command output, paths, or log contents here.
        print("私有诊断保存失败，请检查私有仓库权限与 GitHub 网络。", file=sys.stderr)
        sys.exit(1)
