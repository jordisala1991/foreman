"""lib/main.py in a fresh process, without paying Python's start and foreman's imports on every call.

Most of the suite's CPU went to starting the interpreter and importing foreman for each `lib/main.py` a test runs
(thousands per pass, ~30 ms each on 3.14 and ~100 ms on 3.9). `run` gives the same result as
`subprocess.run([sys.executable, ENTRY, *args], cwd=…, env=…, input=…, text=True, capture_output=True)`: a new
process with its own pid, environment, folder, stdin, stdout, stderr and exit code, started by forking a server
process that imported foreman's modules once and has run nothing else (so no state of a test or of an earlier call
leaks into it). The child runs lib/main.py as Python runs a script: `__main__`, SystemExit, atexit and thread joins as
usual. Its stdout and stderr are pipes read to their end, like subprocess.run's.

Per call: the server forks a monitor, the monitor forks the command, reports its pid and then its exit status and
CPU seconds (`child_cpu` adds them up) on a socket of that call, and exits. The ends of the call (that socket, stdin,
stdout, stderr) go to the server as fds over a unix socket. The server ends when the process that started it does
(its stdin closes).

Off (plain subprocess.run) when FOREMAN_TEST_FORK=0, under coverage (its tracer does not follow a fork) and where
fork or passing fds is missing. A server that cannot start falls back the same way, once per process.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import locale
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "lib/main.py"

_lock = threading.Lock()
child_cpu = 0.0  # user+sys of the commands run here so far (the selftest's per-class CPU adds it)
START_S = 60  # the most a monitor may take to answer with its command's pid
_server = None  # (Popen, socket) once started; False when it cannot run in this process


def enabled():
    return (
        os.environ.get("FOREMAN_TEST_FORK", "1") != "0"
        and hasattr(os, "fork")
        and hasattr(socket, "send_fds")
        and "coverage" not in sys.modules
        and not os.environ.get("COVERAGE_RCFILE")
    )


def _start():
    global _server
    if _server is None:
        try:
            ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            env = {k: v for k, v in os.environ.items() if k != "FOREMAN_TEST_FORK"}
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), str(theirs.fileno())],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                pass_fds=(theirs.fileno(),),
                env=env,
                cwd=str(ROOT),
            )
            theirs.close()
            atexit.register(_stop, proc)
            if proc.stdout.readline() != b"ready\n":  # its imports failed: plain subprocesses show why
                proc.kill()
                proc.wait()
                _server = False
            else:
                _server = (proc, ours)
        except OSError:
            _server = False
    return _server


def _stop(proc):
    """At this process's exit: the server ends and is waited for, so its CPU (and its commands') counts as ours."""
    try:
        proc.stdin.close()
        proc.wait(30)
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()
        proc.wait()


def run(args, cwd, env, input=None, timeout=None):
    """subprocess.run([sys.executable, ENTRY, *args], …, text=True, capture_output=True, check=False)."""
    cmd = [sys.executable, str(ENTRY), *args]
    with _lock:
        server = _start() if enabled() else False
    if not server:
        return subprocess.run(
            cmd, cwd=cwd, input=input, text=True, capture_output=True, env=env, timeout=timeout, check=False
        )
    _proc, main = server
    conn, theirs = socket.socketpair()
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    stdin = tempfile.TemporaryFile()
    if input:
        stdin.write(input.encode(locale.getpreferredencoding(False)))
        stdin.seek(0)
    # our copies of the passed ends stay open until the monitor has them: macOS's collector of fds in flight on
    # unix sockets may otherwise close them under load (seen as an empty read in the monitor)
    try:
        with _lock:
            socket.send_fds(main, [b"x"], [theirs.fileno(), stdin.fileno(), out_w, err_w])
        request = json.dumps({"argv": [str(ENTRY), *args], "cwd": str(cwd), "env": dict(env)}).encode()
        conn.sendall(len(request).to_bytes(8, "big") + request)
        conn.settimeout(START_S)
        control = b""
        while b"\n" not in control:
            part = conn.recv(4096)
            if not part:
                break
            control += part
        conn.settimeout(None)
    except BaseException:
        for fd in (out_r, err_r):
            os.close(fd)
        conn.close()
        raise
    finally:
        theirs.close()
        os.close(out_w)
        os.close(err_w)
        stdin.close()
    return _collect(conn, out_r, err_r, cmd, timeout, control)


def _collect(conn, out_r, err_r, cmd, timeout, control):
    """Reads stdout and stderr to their end and the monitor's answer (pid, exit code, CPU seconds; one line each) to
    its end, like subprocess.run: a timeout kills the command and raises TimeoutExpired."""
    global child_cpu
    deadline = None if timeout is None else time.monotonic() + timeout
    chunks = {out_r: [], err_r: []}
    sel = selectors.DefaultSelector()
    for fd in (out_r, err_r):
        sel.register(fd, selectors.EVENT_READ)
    sel.register(conn, selectors.EVENT_READ)
    try:
        while sel.get_map():
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                with contextlib.suppress(OSError, ValueError):
                    os.kill(int(control.split(b"\n")[0]), signal.SIGKILL)
                raise subprocess.TimeoutExpired(
                    cmd, timeout, _text(b"".join(chunks[out_r])), _text(b"".join(chunks[err_r]))
                )
            for key, _ in sel.select(left):
                fd = key.fd
                data = conn.recv(4096) if key.fileobj is conn else os.read(fd, 65536)
                if not data:
                    sel.unregister(key.fileobj)
                elif key.fileobj is conn:
                    control += data
                else:
                    chunks[fd].append(data)
    finally:
        sel.close()
        conn.close()
        os.close(out_r)
        os.close(err_r)
    lines = control.split(b"\n")
    if len(lines) < 4:
        raise RuntimeError(f"forkentry: no exit status for {cmd[2:]} (the monitor ended early)")
    child_cpu += float(lines[2])
    return subprocess.CompletedProcess(
        cmd, int(lines[1]), _text(b"".join(chunks[out_r])), _text(b"".join(chunks[err_r]))
    )


def _text(data):
    """As subprocess.run(text=True) decodes: the locale's encoding and universal newlines."""
    return io.TextIOWrapper(io.BytesIO(data), encoding=locale.getpreferredencoding(False)).read()


# ---------- the server ----------


def _preload():
    """Every module of foreman (what any command may import), and nothing run."""
    sys.path.insert(0, str(ROOT / "lib"))
    import importlib
    import pkgutil

    import foreman

    for m in pkgutil.iter_modules(foreman.__path__):
        importlib.import_module(f"foreman.{m.name}")


def _serve(fd):
    """The server's loop. Returns only in a command process, with its request; the caller then runs lib/main.py."""
    main = socket.socket(fileno=fd)
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    sel = selectors.DefaultSelector()
    sel.register(main, selectors.EVENT_READ)
    sel.register(sys.stdin, selectors.EVENT_READ)
    while True:
        _reap(os.WNOHANG)
        for key, _ in sel.select(1):
            if key.fileobj is sys.stdin:  # the process that started us is gone: wait for the monitors, then end
                _reap(0)
                os._exit(0)
            _msg, fds, _flags, _addr = socket.recv_fds(main, 16, 4)
            if len(fds) != 4:
                for f in fds:
                    os.close(f)
                continue
            if os.fork() == 0:
                sel.close()
                main.close()
                return _monitor(*fds)
            for f in fds:
                os.close(f)


def _reap(flags):
    """Waits for the monitors that ended (all of them with flags=0): waited for, their CPU counts in ours."""
    while True:
        try:
            pid, _ = os.waitpid(-1, flags)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _monitor(conn_fd, in_fd, out_fd, err_fd):
    """A monitor: never returns, except in its command process, with the request. What fails here goes to the
    server's stderr (the worker's log) and the caller sees no exit status."""
    try:
        return _monitor_call(conn_fd, in_fd, out_fd, err_fd)
    except BaseException:  # noqa: BLE001 (a monitor ends here whatever happened)
        import traceback

        traceback.print_exc()
        os._exit(3)


def _monitor_call(conn_fd, in_fd, out_fd, err_fd):
    conn = socket.socket(fileno=conn_fd)
    head = b""
    while len(head) < 8:
        part = conn.recv(8 - len(head))
        if not part:
            raise RuntimeError(f"eof in head {head!r}")
        head += part
    n, body = int.from_bytes(head, "big"), b""
    while len(body) < n:
        part = conn.recv(n - len(body))
        if not part:
            raise RuntimeError(f"eof in body {len(body)}/{n}")
        body += part
    request = json.loads(body)
    pid = os.fork()
    if pid == 0:
        conn.close()
        for target, fd in ((0, in_fd), (1, out_fd), (2, err_fd)):
            os.dup2(fd, target)
        for fd in (in_fd, out_fd, err_fd):
            os.close(fd)
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, signal.SIG_DFL if s != signal.SIGINT else signal.default_int_handler)
        os.chdir(request["cwd"])
        os.environ.clear()
        os.environ.update(request["env"])
        tempfile.tempdir = None  # $TMPDIR is this call's
        sys.argv = request["argv"]
        return request
    for fd in (in_fd, out_fd, err_fd):
        os.close(fd)
    try:
        conn.sendall(f"{pid}\n".encode())
        _, st, use = os.wait4(pid, 0)
        code = os.waitstatus_to_exitcode(st) if hasattr(os, "waitstatus_to_exitcode") else _exitcode(st)
        conn.sendall(f"{code}\n{use.ru_utime + use.ru_stime:.6f}\n".encode())
    finally:
        os._exit(0)


def _exitcode(st):
    return -os.WTERMSIG(st) if os.WIFSIGNALED(st) else os.WEXITSTATUS(st)


def _run_main():
    """lib/main.py as the interpreter runs a script, and its exit as the interpreter's: SystemExit's code (a text
    printed, 1), an uncaught exception's traceback and 1, KeyboardInterrupt as SIGINT; then the non-daemon threads,
    the atexit functions and a flush of stdout and stderr. The teardown of the modules is skipped (os._exit): the
    imports are the server's and nothing of a command depends on their removal."""
    import atexit
    import runpy
    import threading
    import traceback

    code = 0
    try:
        runpy.run_path(str(ENTRY), run_name="__main__")
    except SystemExit as e:
        code = _exit_code(e.code)
    except KeyboardInterrupt:
        _finish()
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGINT)
    except BaseException:  # noqa: BLE001 (what the interpreter does with it)
        traceback.print_exc()
        code = 1
    threading._shutdown()
    try:
        atexit._run_exitfuncs()
    except SystemExit as e:
        code = _exit_code(e.code)
    os._exit(_finish(code))


def _exit_code(c):
    if c is None:
        return 0
    if isinstance(c, int):
        return c & 0xFF
    with contextlib.suppress(Exception):
        sys.stderr.write(f"{c}\n")
    return 1


def _finish(code=0):
    """Flushes stdout and stderr; 120 when stdout cannot be flushed, as the interpreter exits."""
    for f in (sys.stdout, sys.stderr):
        try:
            f.flush()
        except Exception:  # noqa: BLE001
            if f is sys.stdout and code == 0:
                code = 120
    return code


if __name__ == "__main__":
    _preload()
    _serve(int(sys.argv[1]))
    # a command process: lib/main.py as `python lib/main.py ARGS…` runs it (sys.path[0] is lib/, set by _preload)
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
    _run_main()
