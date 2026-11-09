"""Mutual exclusion for local bot and manual reconciliation (Linux)."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import os

OWNER_REF = "prototype-local-google-profile"
LOCK_PATH = Path(__file__).resolve().parent.parent / ".booking-runtime.lock"

@contextmanager
def runtime_guard():
    # Catch a bot started before the lock was introduced. Refuse conservatively
    # on any other local Python process whose script argument is bot.py.
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            args = (process / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            continue
        if args and b"python" in Path(os.fsdecode(args[0])).name.encode():
            if any(Path(os.fsdecode(arg)).name == "bot.py" for arg in args[1:] if arg):
                raise RuntimeError("Arrestare il bot prima della riconciliazione")
    # Never unlink: all processes must lock the same inode.
    with LOCK_PATH.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Bot o riconciliazione già attivi") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
