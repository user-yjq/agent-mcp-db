"""无网络环境的 wheel 构建（PEP 427 纯 Python wheel，标准库实现）。

正式发布请优先使用 `uv build` / `python -m build`（会拉取 hatchling build backend）；
本脚本作为离线/内网环境的回退，产物结构与入口与正式构建一致。
用法: python scripts/build_wheel.py  （输出到 dist/）
"""

from __future__ import annotations

import hashlib
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DIST = ROOT / "dist"
PKG = "db_assistant_mcp"


def _hash_bytes(data: bytes) -> str:
    return f"sha256={hashlib.sha256(data).hexdigest()}"


def _collect_package_files() -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    root = SRC / PKG
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".pyc"):
            rel = path.relative_to(SRC).as_posix()
            files.append((rel, path.read_bytes()))
    return files


def _metadata(pyproject: dict) -> str:
    project = pyproject["project"]
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {project['name']}",
        f"Version: {project['version']}",
        f"Summary: {project['description']}",
        "License: MIT",
        f"Requires-Python: {project['requires-python']}",
    ]
    for dep in project.get("dependencies", []):
        lines.append(f"Requires-Dist: {dep}")
    return "\n".join(lines) + "\n"


def _entry_points(pyproject: dict) -> str:
    scripts = pyproject["project"]["scripts"]
    lines = ["[console_scripts]"]
    for name, target in scripts.items():
        lines.append(f"{name} = {target}")
    return "\n".join(lines) + "\n"


def build() -> Path:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    dist_name = pyproject["project"]["name"].replace("-", "_")
    wheel_name = f"{dist_name}-{version}-py3-none-any.whl"
    dist_info = f"{dist_name}-{version}.dist-info"

    DIST.mkdir(exist_ok=True)
    wheel_path = DIST / wheel_name

    records: list[tuple[str, str, str]] = []
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 包文件
        for rel, data in _collect_package_files():
            zf.writestr(rel, data)
            records.append((rel, _hash_bytes(data), str(len(data))))
        # dist-info
        for name, data in (
            ("METADATA", _metadata(pyproject).encode()),
            ("WHEEL", b"Wheel-Version: 1.0\nGenerator: db-assistant-build-wheel\nRoot-Is-Purelib: true\nTag: py3-none-any\n"),
            ("entry_points.txt", _entry_points(pyproject).encode()),
        ):
            zf.writestr(f"{dist_info}/{name}", data)
            records.append((f"{dist_info}/{name}", _hash_bytes(data), str(len(data))))
        # RECORD（最后写入，自身不含哈希）
        record_lines = [f"{name},sha256={digest.split('=', 1)[1]},{size}" for name, digest, size in records]
        record_data = ("\n".join(record_lines) + f"\n{dist_info}/RECORD,,\n").encode()
        zf.writestr(f"{dist_info}/RECORD", record_data)

    return wheel_path


if __name__ == "__main__":
    path = build()
    print(f"built: {path} ({path.stat().st_size} bytes)")
