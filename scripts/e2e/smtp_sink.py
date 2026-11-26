"""Local SMTP sink: accepts mail on 127.0.0.1 and stores it; nothing is relayed anywhere."""
import json
import sys
import time
from pathlib import Path

from aiosmtpd.controller import Controller

port, out = int(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)


class Sink:
    async def handle_DATA(self, server, session, envelope):
        n = len(list(out.glob("*.json")))
        (out / f"{n:04d}.json").write_text(json.dumps({
            "rcpt": envelope.rcpt_tos, "from": envelope.mail_from,
            "raw": envelope.content.decode("utf-8", "replace"), "at": time.time()}))
        return "250 OK (sink)"


ctl = Controller(Sink(), hostname="127.0.0.1", port=port)
ctl.start()
print(f"sink listening on {port}", flush=True)
try:
    while True:
        time.sleep(1)
finally:
    ctl.stop()
