#!/usr/bin/env python
"""Unified dev server manager.

Usage:
  python dev_servers.py

Then choose:
  1 -> start backend + frontend
  2 -> stop backend + frontend
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173


def _powershell_executable() -> str:
    win_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(win_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.exists():
        return str(candidate)
    return "powershell"


def _resolve_python_executable(repo_root: Path) -> str:
    candidates = [
        repo_root / "backend" / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    if shutil.which("python"):
        return "python"
    raise RuntimeError("Python executable not found. Please install Python or create .venv.")


def _test_python_module(python_exe: str, module_name: str) -> bool:
    try:
        result = subprocess.run(
            [python_exe, "-c", f"import {module_name}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def _ensure_backend_dependencies(python_exe: str, backend_dir: Path) -> None:
    if _test_python_module(python_exe, "uvicorn"):
        return

    requirements = backend_dir / "requirements.txt"
    if not requirements.exists():
        raise RuntimeError(f"uvicorn is missing and requirements.txt not found: {requirements}")

    print(f"[backend] uvicorn not found in '{python_exe}'. Installing backend requirements...")
    result = subprocess.run([python_exe, "-m", "pip", "install", "-r", str(requirements)], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to install backend requirements with {python_exe}")

    if not _test_python_module(python_exe, "uvicorn"):
        raise RuntimeError(f"uvicorn still missing after install. Please check Python environment: {python_exe}")


def _load_state(state_file: Path) -> dict[str, Any] | None:
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state_file.unlink(missing_ok=True)
        print(f"State file invalid and removed: {state_file}")
        return None


def _stop_pid_tree(pid: int, name: str) -> None:
    # taskkill /T kills child processes as well.
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print(f"Stopped {name} (PID: {pid}).")
        return

    output = (result.stdout + result.stderr).strip().lower()
    if "not found" in output or "没有运行的实例" in output or "no running instance" in output:
        print(f"{name} already stopped (PID: {pid}).")
    else:
        print(f"Failed to stop {name} (PID: {pid}): {(result.stdout + result.stderr).strip()}")


def stop_servers(repo_root: Path, *, quiet_when_missing: bool = False) -> None:
    state_file = repo_root / ".dev-servers.json"
    state = _load_state(state_file)
    if not state:
        if not quiet_when_missing:
            print(f"No running dev state found ({state_file}).")
        return

    for key in ("backend_pid", "frontend_pid"):
        value = state.get(key)
        if value is None:
            continue
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        _stop_pid_tree(pid, key)

    state_file.unlink(missing_ok=True)
    print(f"Removed state file: {state_file}")


def start_servers(repo_root: Path) -> None:
    backend_dir = repo_root / "backend"
    frontend_dir = repo_root / "frontend"
    state_file = repo_root / ".dev-servers.json"

    if not backend_dir.exists():
        raise RuntimeError(f"Missing backend directory: {backend_dir}")
    if not frontend_dir.exists():
        raise RuntimeError(f"Missing frontend directory: {frontend_dir}")

    # Keep behavior aligned with old start script: stop previous tracked processes first.
    stop_servers(repo_root, quiet_when_missing=True)

    python_exe = _resolve_python_executable(repo_root)
    _ensure_backend_dependencies(python_exe, backend_dir)
    ps_exe = _powershell_executable()

    backend_command = f"""
Set-Location -LiteralPath '{backend_dir}'
Write-Host '[backend] starting on http://{BACKEND_HOST}:{BACKEND_PORT}'
Write-Host '[backend] python: {python_exe}'
& '{python_exe}' -m uvicorn app.main:app --reload --host {BACKEND_HOST} --port {BACKEND_PORT}
"""

    frontend_command = f"""
Set-Location -LiteralPath '{frontend_dir}'
Write-Host '[frontend] starting on http://{FRONTEND_HOST}:{FRONTEND_PORT}'
if (-not (Test-Path 'node_modules')) {{
  Write-Host '[frontend] node_modules missing, running npm install...'
  npm install
}}
npm run dev -- --host {FRONTEND_HOST} --port {FRONTEND_PORT}
"""

    create_console_flag = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

    backend_proc = subprocess.Popen(
        [ps_exe, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", backend_command],
        creationflags=create_console_flag,
    )
    frontend_proc = subprocess.Popen(
        [ps_exe, "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", frontend_command],
        creationflags=create_console_flag,
    )

    state = {
        "backend_pid": backend_proc.pid,
        "frontend_pid": frontend_proc.pid,
        "backend_url": f"http://{BACKEND_HOST}:{BACKEND_PORT}",
        "frontend_url": f"http://{FRONTEND_HOST}:{FRONTEND_PORT}",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Backend started (PID: {backend_proc.pid}) -> http://{BACKEND_HOST}:{BACKEND_PORT}")
    print(f"Frontend started (PID: {frontend_proc.pid}) -> http://{FRONTEND_HOST}:{FRONTEND_PORT}")
    print(f"State saved to: {state_file}")


def main() -> int:
    repo_root = Path(__file__).resolve().parent

    if len(sys.argv) >= 2:
        # Optional non-interactive mode.
        mode = sys.argv[1].strip().lower()
        if mode in {"1", "start"}:
            start_servers(repo_root)
            return 0
        if mode in {"2", "stop"}:
            stop_servers(repo_root)
            return 0

    print("请选择操作:")
    print("  1. 启动前后端开发服务")
    print("  2. 停止前后端开发服务")
    choice = input("请输入序号 (1/2): ").strip()

    if choice == "1":
        start_servers(repo_root)
        return 0
    if choice == "2":
        stop_servers(repo_root)
        return 0

    print("无效输入，请输入 1 或 2。")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCanceled.")
        raise SystemExit(130)
