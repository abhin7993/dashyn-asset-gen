"""
Test script for DashynAssetGen RunPod endpoint.
Sends a test request, polls for completion, and saves the output zip.
"""

import os
import requests
import json
import time
import base64
import sys

API_KEY = os.environ.get("RUNPOD_API_KEY", "YOUR_API_KEY_HERE")
ENDPOINT = os.environ.get("RUNPOD_ENDPOINT_ID", "YOUR_ENDPOINT_ID")
URL = f"https://api.runpod.ai/v2/{ENDPOINT}"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

PAYLOAD = {
    "input": {
        "vibe_name": "mughal_royale",
        "vibe_description": "Mughal era royal court, rich jewel tones, gold embroidery, ornate architecture",
        "num_assets": 2,
    }
}


def main():
    print(f"Sending request to {URL}/run")
    print(f"Payload: {json.dumps(PAYLOAD, indent=2)}")
    print()

    # Submit job
    r = requests.post(f"{URL}/run", json=PAYLOAD, headers=HEADERS)
    if r.status_code != 200:
        print(f"ERROR: {r.status_code} {r.text}")
        sys.exit(1)

    job_id = r.json()["id"]
    status = r.json().get("status")
    print(f"Job submitted: {job_id} (status: {status})")

    # Poll for completion
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > 900:  # 15 min timeout (first run downloads models)
            print("Timeout after 15 minutes!")
            sys.exit(1)

        time.sleep(5)
        r = requests.get(f"{URL}/status/{job_id}", headers=HEADERS)
        result = r.json()
        status = result.get("status")
        print(f"  [{elapsed:.0f}s] {status}")

        if status == "COMPLETED":
            output = result.get("output", {})

            if "error" in output:
                print(f"\nWorker returned error: {output['error']}")
                sys.exit(1)

            vibe_name = output.get("vibe_name", "output")
            total = output.get("total_images", 0)
            warnings = output.get("warnings", [])

            print(f"\nDone in {elapsed:.0f}s!")
            print(f"  Vibe: {vibe_name}")
            print(f"  Images: {total}")

            if warnings:
                print(f"  Warnings: {len(warnings)}")
                for w in warnings:
                    print(f"    - {w}")

            # Save zip
            zip_b64 = output.get("zip_base64")
            if zip_b64:
                filename = f"{vibe_name}.zip"
                with open(filename, "wb") as f:
                    f.write(base64.b64decode(zip_b64))
                print(f"\n  Saved: {filename}")
            else:
                print("\n  No zip_base64 in response!")

            break

        elif status == "FAILED":
            print(f"\nFAILED: {json.dumps(result, indent=2)}")
            sys.exit(1)


if __name__ == "__main__":
    main()
