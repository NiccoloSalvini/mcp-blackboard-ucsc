"""Download the Learn REST API swagger that matches the server's version into docs/.

    PYTHONPATH=src .venv/bin/python scripts/fetch_swagger.py

Reads BB_BASE_URL from the environment (.env), asks /v1/system/version, and
fetches https://devportal-docstore.s3.amazonaws.com/learn-swagger-<version>.json.
bb_api_doc searches the newest file in docs/.
"""
import json, os, pathlib, sys, urllib.request

base = os.environ.get("BB_BASE_URL", "").rstrip("/")
if not base:
    sys.exit("set BB_BASE_URL (source .env)")
with urllib.request.urlopen(f"{base}/learn/api/public/v1/system/version", timeout=30) as r:
    v = json.load(r)["learn"]
version = f"{v['major']}.{v['minor']}.{v['patch']}"
url = f"https://devportal-docstore.s3.amazonaws.com/learn-swagger-{version}.json"
out = pathlib.Path(__file__).resolve().parent.parent / "docs" / f"learn-swagger-{version}.json"
with urllib.request.urlopen(url, timeout=60) as r:
    out.write_bytes(r.read())
print(f"{version} -> {out} ({out.stat().st_size // 1024} KB)")
