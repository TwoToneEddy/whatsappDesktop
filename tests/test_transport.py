import os
import sys
import threading
import time

import pytest

from whatsapp_terminal.transport import Bridge, bridge_path


@pytest.fixture
def transport(monkeypatch, tmp_path):
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / ".ready").touch()
    (helper / "bridge.mjs").write_text(
        "import json, sys, time\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    method = request['method']\n"
        "    if method == 'die': sys.exit(1)\n"
        "    if method == 'fail':\n"
        "        print('Startup failed', file=sys.stderr, flush=True)\n"
        "        sys.exit(2)\n"
        "    if method == 'timeout': continue\n"
        "    if method == 'event':\n"
        "        print(json.dumps({'event': 'test'}), flush=True)\n"
        "    print(json.dumps({'id': request['id'], 'result': request['params']}), flush=True)\n"
        "    if method == 'stop': sys.exit(0)\n"
    )
    monkeypatch.setattr("whatsapp_terminal.transport.node_binary", lambda: sys.executable)
    monkeypatch.setattr("whatsapp_terminal.transport.bridge_path", lambda: helper)
    data = tmp_path / "data"
    data.mkdir()
    bridge = Bridge(data, lambda event: None)
    yield bridge
    bridge.stop()


def test_packaged_bridge_has_all_required_files():
    assert len(bridge_path().name) == 16


def test_event_callback_can_make_request_without_deadlock(transport):
    done = threading.Event()

    def callback(event):
        if event.get("event") == "test":
            assert transport.request("echo", value="nested") == {"value": "nested"}
            done.set()

    transport.on_event = callback
    transport.start()
    assert transport.request("event", value="outer") == {"value": "outer"}
    assert done.wait(2)
    transport.stop()
    assert transport.process.poll() is not None
    assert transport.session_lock is None


def test_timeout_is_explicit_and_does_not_retry(transport):
    transport.start()
    assert os.getpgid(transport.process.pid) == transport.process.pid
    with pytest.raises(RuntimeError, match="result is unknown"):
        transport.request("timeout", timeout=0.02)
    assert not transport.pending
    assert transport.request("echo", value="next") == {"value": "next"}


def test_crashed_helper_wakes_pending_requests(transport):
    transport.start()
    with pytest.raises(RuntimeError, match="helper exited"):
        transport.request("die")


def test_second_process_cannot_open_same_session(transport):
    transport.start()
    duplicate = Bridge(transport.data_dir, lambda event: None)
    with pytest.raises(BlockingIOError):
        duplicate.start()
    assert duplicate.session_lock is None
    assert transport.request("echo") == {}


def test_helper_exit_reports_status_and_drains_stderr(transport):
    events = []
    done = threading.Event()
    original = transport._stderr

    def slow_stderr():
        time.sleep(0.1)
        original()

    def receive(event):
        events.append(event)
        if event.get("event") == "connection":
            done.set()

    transport._stderr = slow_stderr
    transport.on_event = receive
    transport.start()
    with pytest.raises(RuntimeError, match="helper exited"):
        transport.request("fail")
    assert done.wait(3)
    assert events[0] == {"event": "notice", "text": "Startup failed"}
    assert "status 2" in events[-1]["error"]
    assert "not reconnecting" in events[-1]["error"]
