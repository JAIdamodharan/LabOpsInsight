"""Sandboxed Python execution for the agent (statistics, charts).

DockerSandbox is the real boundary: no network, read-only root FS, dropped capabilities,
non-root user, memory/CPU/pid limits, DB mounted read-only, wall-clock kill.
LocalSandbox is a development convenience only: it scrubs env vars and enforces timeouts
but does NOT isolate the filesystem or network. Never use it with untrusted prompts.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_OUTPUT = 4000
TIMEOUT_S = 30
IMAGE = "analyst-sandbox"
# matplotlib rebuilds its font cache (~20s) in a fresh config dir, so keep one persistent.
MPL_CACHE = Path.home() / ".cache" / "analyst-sandbox" / "mpl"

# Runs inside the sandbox before the model's code. `con` is a read-only DB handle.
PREAMBLE = '''\
import os, duckdb
DB_PATH, OUT_DIR = os.environ["DB_PATH"], os.environ["OUT_DIR"]
con = duckdb.connect(DB_PATH, read_only=True, config={"enable_external_access": False})
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd, numpy as np
'''


@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    files: list[Path] = field(default_factory=list)

    def to_text(self) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip()[-MAX_OUTPUT:])
        if self.timed_out:
            parts.append(f"TIMEOUT: killed after {TIMEOUT_S}s")
        elif self.exit_code != 0:
            parts.append("ERROR:\n" + self.stderr.strip()[-MAX_OUTPUT:])
        if self.files:
            parts.append("Saved files: " + ", ".join(f.name for f in self.files))
        return "\n".join(parts) or "(no output; use print() to see results)"

    @property
    def is_error(self) -> bool:
        return self.timed_out or self.exit_code != 0


class _Base:
    def __init__(self, db_path: str, artifacts_dir: str = "artifacts"):
        self.db_path = str(Path(db_path).resolve())
        self.artifacts_dir = Path(artifacts_dir)

    def _collect(self, out: Path) -> list[Path]:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in sorted(out.glob("*")):
            if f.is_file() and f.suffix.lower() in {".png", ".svg", ".csv"}:
                dest = self.artifacts_dir / f"{uuid.uuid4().hex[:8]}_{f.name}"
                shutil.copy(f, dest)
                saved.append(dest)
        return saved


class DockerSandbox(_Base):
    def build_cmd(self, name: str, work: Path) -> list[str]:
        return [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "-v", f"{self.db_path}:/data/db.duckdb:ro",
            "-v", f"{work}:/work:rw",
            "-e", "DB_PATH=/data/db.duckdb", "-e", "OUT_DIR=/work/out",
            IMAGE, "python", "/work/run.py",
        ]

    def run(self, code: str) -> SandboxResult:
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "out").mkdir()
            os.chmod(work, 0o777), os.chmod(work / "out", 0o777)
            (work / "run.py").write_text(PREAMBLE + "\n" + code)
            name = f"analyst-{uuid.uuid4().hex[:8]}"
            try:
                p = subprocess.run(self.build_cmd(name, work), capture_output=True, text=True, timeout=TIMEOUT_S)
                return SandboxResult(p.stdout, p.stderr, p.returncode, False, self._collect(work / "out"))
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "kill", name], capture_output=True)
                return SandboxResult(timed_out=True, exit_code=137)


class LocalSandbox(_Base):
    """DEV ONLY - not isolated. See module docstring."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        MPL_CACHE.mkdir(parents=True, exist_ok=True)
        if not any(MPL_CACHE.glob("fontlist*.json")):  # one-off ~20s build, keep it out of the 30s query budget
            subprocess.run([sys.executable, "-I", "-c", "import matplotlib.pyplot"],
                           env={"MPLCONFIGDIR": str(MPL_CACHE), "MPLBACKEND": "Agg"}, capture_output=True)

    def run(self, code: str) -> SandboxResult:
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "out").mkdir()
            script = work / "run.py"
            script.write_text(PREAMBLE + "\n" + code)
            MPL_CACHE.mkdir(parents=True, exist_ok=True)
            env = {"PATH": os.environ.get("PATH", ""), "DB_PATH": self.db_path,
                   "OUT_DIR": str(work / "out"), "MPLCONFIGDIR": str(MPL_CACHE), "HOME": td}
            try:
                p = subprocess.run([sys.executable, "-I", str(script)], cwd=td, env=env,
                                   capture_output=True, text=True, timeout=TIMEOUT_S)
                return SandboxResult(p.stdout, p.stderr, p.returncode, False, self._collect(work / "out"))
            except subprocess.TimeoutExpired:
                return SandboxResult(timed_out=True, exit_code=137)


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False
