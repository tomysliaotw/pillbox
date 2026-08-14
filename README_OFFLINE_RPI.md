# Offline Raspberry Pi 5 deployment

Prepare the device once while it has internet access:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-rpi.txt
```

Copy `36_5.json.zip` to this project directory before taking the Pi offline.
Import it once with `python3 tfda_cloud_sync.py`; this uses the local archive by
default and does not make a network request. Start the application with
`python3 run_pillbox.py` (or the compatibility command `python3 main.py`).

`AgentSupervisor` is the allow-listed interface for a local LLM/voice frontend.
It exposes medication, scheduling, status, and local-TFDA-import operations;
it does not expose shell commands, arbitrary SQL, or changes to health rules.
