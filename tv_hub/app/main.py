#!/usr/bin/env python3
"""TV Hub: startpunkt."""
import signal
import threading

from hub import VERSION, ai, catalog, db, devices, diagnostics, extras, info, portals, recordings, taste
from hub.config import CFG
from hub.server import start_servers


def housekeeping(stop_event):
    while not stop_event.is_set():
        try:
            devices.cleanup()
            diagnostics.cleanup()
            recordings.expire()
        except Exception as exc:
            print(f"[fel] städning: {exc}", flush=True)
        stop_event.wait(600)


def main():
    db.connect()
    catalog.mark_adult()
    catalog.backfill_years()
    _ = extras, taste  # registrerar bevakningskontrollen och Filmkvällens katalog efter synk
    stop_event = threading.Event()
    for target in (portals.monitor_loop, catalog.sync_loop, info.fetch_loop, housekeeping, ai.loop):
        threading.Thread(target=target, args=(stop_event,), daemon=True, name=target.__name__).start()
    admin, device = start_servers()
    print(
        f"TV Hub {VERSION} startad. Admin på port {CFG['admin_port']}, boxar på port {CFG['device_port']}.",
        flush=True,
    )

    def shutdown(*_):
        stop_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    stop_event.wait()
    admin.shutdown()
    device.shutdown()
    print("TV Hub stoppad.", flush=True)


if __name__ == "__main__":
    main()
