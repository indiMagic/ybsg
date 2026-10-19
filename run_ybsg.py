"""PyInstaller entry point."""
import os
import sys


def _restore_child_env() -> None:
    """The one-file bootloader points LD_LIBRARY_PATH at its temporary folder so the bundle finds its own libraries.
    Programs YBSG starts (Chrome for the app window, xdg-open, git, dtc, gs, kas) inherit it, load the bundled
    libraries instead of the system ones and crash. ld.so read the variable when this process started, so putting the
    original back only affects child processes."""
    if not getattr(sys, "frozen", False):
        return
    for var in ("LD_LIBRARY_PATH", "LIBPATH", "DYLD_LIBRARY_PATH"):
        orig = os.environ.pop(var + "_ORIG", None)
        if orig is not None:
            os.environ[var] = orig
        else:
            os.environ.pop(var, None)


_restore_child_env()
from ybsg.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
