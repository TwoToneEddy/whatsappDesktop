import hashlib
import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from platformdirs import user_cache_path


def node_binary():
    node = shutil.which("node")
    if not node:
        raise OSError("Node.js 22 or newer is required. Install it, then run wat --setup-backend.")
    result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
    try:
        major = int(result.stdout.strip().lstrip("v").split(".")[0])
    except ValueError:
        major = 0
    if result.returncode or major < 22:
        raise OSError(f"Node.js 22 or newer is required: {result.stderr or result.stdout}")
    return node


def bridge_path():
    source = Path(__file__).with_name("bridge")
    digest = hashlib.sha256()
    for name in ("package.json", "package-lock.json", "bridge.mjs", ".npmrc"):
        digest.update((source / name).read_bytes())
    return user_cache_path("whatsapp-terminal") / "baileys" / digest.hexdigest()[:16]


def setup_backend():
    node_binary()
    npm = shutil.which("npm")
    if not npm:
        raise OSError("npm is required for backend setup (normally installed with Node.js).")
    target = bridge_path()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = target / ".ready"
    marker.unlink(missing_ok=True)
    for name in ("package.json", "package-lock.json", "bridge.mjs", ".npmrc"):
        shutil.copyfile(Path(__file__).with_name("bridge") / name, target / name)
    result = subprocess.run(
        [npm, "ci", "--ignore-scripts", "--legacy-peer-deps", "--omit=dev"], cwd=target
    )
    if result.returncode:
        raise RuntimeError("npm ci failed; fix the reported error and rerun --setup-backend.")
    result = subprocess.run(
        [node_binary(), "--input-type=module", "-e", "await import('baileys')"],
        cwd=target,
    )
    if result.returncode:
        raise RuntimeError("Baileys import failed. Check the Node.js error above.")
    marker.touch(mode=0o600)
    print("Baileys backend installed. Run wat or wat --plain to pair your phone.")


class Bridge:
    def __init__(self, data_dir, on_event):
        self.node = node_binary()
        self.path = bridge_path()
        if not (self.path / ".ready").exists():
            raise OSError("Baileys is not installed for this version. Run wat --setup-backend.")
        self.data_dir = data_dir
        self.on_event = on_event
        self.process = None
        self.pending = {}
        self.lock = threading.Lock()
        self.serial = 0
        self.events = queue.Queue()
        self.session_lock = None
        self.stopping = False

    def start(self):
        import fcntl

        self.session_lock = (self.data_dir / "baileys.lock").open("a")
        try:
            fcntl.flock(self.session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.process = subprocess.Popen(
                [self.node, str(self.path / "bridge.mjs"), str(self.data_dir.resolve())],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                start_new_session=True,
            )
        except Exception:
            self.session_lock.close()
            self.session_lock = None
            raise
        self.stderr_thread = threading.Thread(target=self._stderr, daemon=True)
        threading.Thread(target=self._dispatch, daemon=True).start()
        self.stderr_thread.start()
        threading.Thread(target=self._read, daemon=True).start()

    def _stderr(self):
        for line in self.process.stderr:
            self.events.put({"event": "notice", "text": line.rstrip()})

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if "id" in item:
                    with self.lock:
                        waiter = self.pending.get(item["id"])
                    if waiter:
                        waiter.put(item)
                else:
                    self.events.put(item)
        finally:
            with self.lock:
                for waiter in self.pending.values():
                    waiter.put({"error": "Baileys helper exited. Restart wat."})
            self.stderr_thread.join(timeout=2)
            try:
                status = self.process.wait(timeout=2)
                detail = f"exited with status {status}"
            except subprocess.TimeoutExpired:
                detail = "closed its output stream"
            error = (
                "Disconnected."
                if self.stopping
                else (
                    f"Baileys helper {detail}. It is not reconnecting. "
                    "Check any preceding error, then quit and restart wat."
                )
            )
            self.events.put({"event": "connection", "state": "closed", "error": error})
            self.events.put(None)

    def _dispatch(self):
        while (item := self.events.get()) is not None:
            try:
                self.on_event(item)
            except Exception as error:
                self.on_event({"event": "notice", "text": f"Backend event failed: {error}"})

    def request(self, method, timeout=30, **params):
        waiter = queue.Queue()
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise RuntimeError("Baileys helper is not running.")
            self.serial += 1
            request_id = self.serial
            self.pending[request_id] = waiter
        try:
            with self.lock:
                self.process.stdin.write(
                    json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
                )
                self.process.stdin.flush()
            try:
                result = waiter.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError(
                    f"{method} timed out; its result is unknown. Check your phone before retrying."
                ) from None
            if result.get("error"):
                raise RuntimeError(result["error"])
            return result.get("result")
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def stop(self):
        self.stopping = True
        try:
            if self.process and self.process.poll() is None:
                try:
                    self.request("stop", timeout=5)
                    self.process.wait(timeout=5)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
        finally:
            if self.session_lock:
                self.session_lock.close()
                self.session_lock = None
