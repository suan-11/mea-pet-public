"""常驻 VITS worker 管理器（meapet/tts/engines/vits_worker.py）的协议测试。

用假 serve 脚本（真子进程、无 torch）钉住：就绪握手、按 id 配对、
进程死亡后自动重拉、请求超时杀进程降级、close 收尾。
不能证明：真模型合成质量与延迟（那要真权重 + --speech-latency 验收）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from meapet.tts.engines.vits_worker import VitsServeWorker  # noqa: E402

FAKE_SERVE = """
import json, os, sys, time
print(json.dumps({"event": "ready", "model": "fake"}), flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if req.get("text") == "DIE":
        sys.exit(3)
    if req.get("text") == "HANG":
        time.sleep(30)
    out = req.get("output")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(b"RIFF-fake")
    print(json.dumps({"id": req.get("id"), "ok": True, "output": out}), flush=True)
"""


@pytest.fixture()
def script(tmp_path):
    path = tmp_path / "fake_serve.py"
    path.write_text(FAKE_SERVE, encoding="utf-8")
    return str(path)


def _worker(script, **kwargs):
    params = dict(startup_timeout=30, request_timeout=15)
    params.update(kwargs)
    return VitsServeWorker(sys.executable, script, **params)


def test_synthesize_writes_output(script, tmp_path):
    worker = _worker(script)
    try:
        out = str(tmp_path / "a.wav")
        assert worker.synthesize("你好", out) is True
        assert Path(out).read_bytes() == b"RIFF-fake"
        # 第二次复用同一进程（常驻）
        out2 = str(tmp_path / "b.wav")
        assert worker.synthesize("再见", out2) is True
    finally:
        worker.close()


def test_process_death_then_auto_restart(script, tmp_path):
    worker = _worker(script)
    try:
        assert worker.synthesize("DIE", str(tmp_path / "x.wav")) is False
        # 死进程必须被重拉，下一发照常成功
        assert worker.synthesize("复活", str(tmp_path / "y.wav")) is True
    finally:
        worker.close()


def test_request_timeout_kills_and_degrades(script, tmp_path):
    worker = _worker(script, request_timeout=1)
    try:
        assert worker.synthesize("HANG", str(tmp_path / "z.wav")) is False
        assert worker._proc is None  # 半死进程不留
    finally:
        worker.close()


def test_bad_python_degrades_without_raise(tmp_path):
    worker = VitsServeWorker(
        str(tmp_path / "not-exist-python.exe"), "whatever.py",
        startup_timeout=5, request_timeout=5,
    )
    assert worker.synthesize("你好", str(tmp_path / "o.wav")) is False


def test_close_idempotent(script, tmp_path):
    worker = _worker(script)
    worker.synthesize("你好", str(tmp_path / "c.wav"))
    worker.close()
    worker.close()
    assert worker._proc is None
