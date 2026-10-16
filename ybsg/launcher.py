"""Desktop launcher: native splash (UI-13/UI-14) -> local server -> app window. No runtime needed beyond the bundle."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import webbrowser

from .boot import Boot, log_path
from .branding import brand
from .server import make_server


def _splash(boot: Boot) -> bool:
    """Show a Tk splash while boot runs. Returns False if Tk is unavailable (the web splash is used instead)."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return False
    try:
        b = brand()
        root = tk.Tk()
    except Exception:
        return False
    root.overrideredirect(True)
    w, h = 520, 300
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 2}")
    root.configure(bg="#10151c")
    tk.Label(root, text="BSP", fg="#58a6ff", bg="#10151c", font=("Helvetica", 12, "bold")).pack(pady=(34, 0))
    tk.Label(root, text=b["product"], fg="#ffffff", bg="#10151c", font=("Helvetica", 34, "bold")).pack()
    tk.Label(root, text=b["product_long"], fg="#9aa7b8", bg="#10151c", font=("Helvetica", 11)).pack()
    status = tk.StringVar(value="Starting")
    tk.Label(root, textvariable=status, fg="#c9d4e2", bg="#10151c", font=("Helvetica", 10)).pack(pady=(26, 4))
    bar = ttk.Progressbar(root, length=380, mode="determinate", maximum=len(boot.steps))
    bar.pack()
    tk.Label(root, text=f"Version {b['version']}", fg="#9aa7b8", bg="#10151c", font=("Helvetica", 9)).pack(pady=(10, 0))
    tk.Label(root, text=b["company"], fg="#9aa7b8", bg="#10151c", font=("Helvetica", 9)).pack(pady=(2, 0))
    err_frame = tk.Frame(root, bg="#10151c")

    def tick() -> None:
        st = boot.status()
        done = sum(1 for s in st["steps"] if s["state"] == "done")
        bar["value"] = done
        run = next((s["name"] for s in st["steps"] if s["state"] == "running"), None)
        status.set(run or ("Ready" if st["ready"] else "Starting"))
        if st["error"]:  # UI-14: plain-language error and a View log button instead of closing
            status.set(st["error"])
            if not err_frame.winfo_children():
                tk.Button(err_frame, text="View log", command=lambda: _open(str(log_path()))).pack(side="left", padx=4)
                tk.Button(err_frame, text="Close", command=root.destroy).pack(side="left", padx=4)
                err_frame.pack(pady=8)
            return
        if st["ready"]:
            root.destroy()  # closes by itself, never delays start-up
            return
        root.after(40, tick)

    root.after(40, tick)
    root.mainloop()
    return True


def _open(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            import os
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _window(url: str) -> None:
    """Prefer an app-style window: pywebview, else Chromium/Edge --app, else the default browser."""
    try:
        import webview  # type: ignore
        webview.create_window(f"{brand()['product']} - {brand()['product_long']}", url, width=1360, height=860, min_size=(1000, 640))
        webview.start()
        return
    except Exception:
        pass
    for exe in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(exe)
        if path:
            subprocess.Popen([path, f"--app={url}", "--window-size=1360,860"])
            return
    webbrowser.open(url)


def run_gui(port: int = 0, open_window: bool = True) -> int:
    boot = Boot().start()
    used_native = _splash(boot) if open_window else False
    if not used_native:
        boot._t.join(timeout=0)  # web splash polls /api/boot
    srv, app = make_server(boot, port)
    host, p = srv.server_address[:2]
    url = f"http://127.0.0.1:{p}/" + ("?nosplash=1" if used_native else "")
    print(f"YBSG listening on {url}", flush=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    if open_window:
        _window(url)
        if used_native or True:
            try:
                t.join()
            except KeyboardInterrupt:
                pass
    else:
        try:
            t.join()
        except KeyboardInterrupt:
            pass
    return 0
