from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        print("usage: python -m scripts.replay_webhook examples/webhook_message.json [http://localhost:8000/webhook]")
        return 2
    payload_path = Path(sys.argv[1])
    url = sys.argv[2] if len(sys.argv) == 3 else "http://localhost:8000/webhook"
    data = payload_path.read_bytes()
    json.loads(data)
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.read().decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
