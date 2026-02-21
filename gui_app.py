"""
DashynAssetGen GUI — Streamlit app for generating themed image asset packs.

Run with:
    streamlit run gui_app.py
"""

import base64
import io
import os
import time
import zipfile
from pathlib import Path

import requests
import streamlit as st
from PIL import Image

POLL_INTERVAL = 5
TIMEOUT = 900  # 15 minutes (first run downloads models)
CATEGORIES = ["backgrounds", "female", "male"]

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DashynAssetGen",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("DashynAssetGen")
st.caption("Generate themed image asset packs via RunPod serverless")

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("API Configuration")
    api_key = st.text_input(
        "RunPod API Key",
        value=os.environ.get("RUNPOD_API_KEY", ""),
        type="password",
        help="Set RUNPOD_API_KEY env var or enter here",
    )
    endpoint_id = st.text_input(
        "RunPod Endpoint ID",
        value=os.environ.get("RUNPOD_ENDPOINT_ID", ""),
        help="Set RUNPOD_ENDPOINT_ID env var or enter here",
    )

    st.divider()
    st.header("Output Settings")
    output_dir = st.text_input(
        "Output Directory",
        value="./output",
        help="Where to save unzipped assets",
    )

    st.divider()
    if st.button("Clear Results", use_container_width=True):
        for key in ["last_result", "last_output_dir", "last_vibe_name"]:
            st.session_state.pop(key, None)
        st.rerun()


# ── API functions ────────────────────────────────────────────────────────────


def submit_job(api_key, endpoint_id, vibe_name, vibe_description, num_assets):
    """POST /run — returns (job_id, error_message)."""
    url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "vibe_name": vibe_name,
            "vibe_description": vibe_description,
            "num_assets": num_assets,
        }
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text}"
        return r.json().get("id"), None
    except requests.exceptions.ConnectionError:
        return None, "Connection failed. Check your endpoint ID."
    except Exception as e:
        return None, str(e)


def poll_status(api_key, endpoint_id, job_id):
    """GET /status/{job_id} — returns (status, result_dict, error_message)."""
    url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return None, None, f"HTTP {r.status_code}"
        data = r.json()
        return data.get("status"), data, None
    except Exception as e:
        return None, None, str(e)


def _next_filename(directory, prefix, ext):
    """Find next available numbered filename in directory (e.g. bg_3.png if bg_1, bg_2 exist)."""
    existing = set(p.name for p in directory.glob(f"{prefix}_*{ext}"))
    n = 1
    while f"{prefix}_{n}{ext}" in existing:
        n += 1
    return f"{prefix}_{n}{ext}"


# Map zip filenames to their category prefix
_PREFIX_MAP = {"bg": "bg", "female": "female", "male": "male"}


def decode_and_save_zip(zip_base64, vibe_name, output_dir):
    """Decode base64 zip, save zip, and extract images without overwriting existing ones."""
    try:
        zip_bytes = base64.b64decode(zip_base64)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Save zip with timestamp to avoid overwriting previous zips
        ts = int(time.time())
        zip_path = out / f"{vibe_name}_{ts}.zip"
        zip_path.write_bytes(zip_bytes)

        extract_dir = out / vibe_name
        extract_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue  # skip directory entries
                # member looks like "backgrounds/bg_1.png"
                parts = Path(member)
                category = parts.parent.name  # "backgrounds", "female", "male"
                stem = parts.stem.rsplit("_", 1)[0]  # "bg", "female", "male"
                suffix = parts.suffix  # ".png"

                cat_dir = extract_dir / category
                cat_dir.mkdir(parents=True, exist_ok=True)

                new_name = _next_filename(cat_dir, stem, suffix)
                target = cat_dir / new_name

                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())

        return str(zip_path), str(extract_dir), None
    except Exception as e:
        return None, None, str(e)


# ── Input form ───────────────────────────────────────────────────────────────

with st.form("generate_form"):
    col1, col2 = st.columns([3, 1])
    with col1:
        vibe_name = st.text_input(
            "Vibe Name",
            placeholder="e.g., mughal_royale",
            help="Short identifier (used as folder name)",
        )
        vibe_description = st.text_area(
            "Vibe Description",
            placeholder="e.g., Mughal era royal court, rich jewel tones, gold embroidery, ornate architecture",
            height=100,
            help="Detailed aesthetic description for prompt generation",
        )
    with col2:
        num_assets = st.number_input(
            "Assets per Category",
            min_value=1,
            max_value=10,
            value=2,
            help="Images per category (backgrounds, female, male)",
        )
        st.metric("Total Images", num_assets * 3)

    submitted = st.form_submit_button(
        "Generate Assets", use_container_width=True, type="primary"
    )

# ── Job execution ────────────────────────────────────────────────────────────

if submitted:
    # Validation
    errors = []
    if not api_key:
        errors.append("RunPod API Key is required (set in sidebar)")
    if not endpoint_id:
        errors.append("RunPod Endpoint ID is required (set in sidebar)")
    if not vibe_name:
        errors.append("Vibe Name is required")
    if not vibe_description:
        errors.append("Vibe Description is required")
    if errors:
        for e in errors:
            st.error(e)
        st.stop()

    safe_vibe = vibe_name.strip().replace(" ", "_")

    with st.status("Submitting job...", expanded=True) as status_container:
        job_id, err = submit_job(
            api_key, endpoint_id, safe_vibe, vibe_description, num_assets
        )
        if err:
            st.error(f"Submission failed: {err}")
            status_container.update(label="Submission failed", state="error")
            st.stop()

        st.write(f"Job ID: `{job_id}`")
        status_container.update(label=f"Job queued — {job_id}", state="running")

        # Polling loop
        progress_text = st.empty()
        progress_bar = st.progress(0)
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            if elapsed > TIMEOUT:
                st.error("Timeout after 15 minutes!")
                status_container.update(label="Timed out", state="error")
                st.stop()

            time.sleep(POLL_INTERVAL)
            status_val, result, err = poll_status(api_key, endpoint_id, job_id)

            if err:
                progress_text.markdown(
                    f"**Poll error** (retrying): {err} — {int(elapsed)}s"
                )
                continue

            elapsed_str = f"{int(elapsed)}s"

            if status_val == "IN_QUEUE":
                progress_bar.progress(0.1)
                progress_text.markdown(
                    f"**IN_QUEUE** — Waiting for worker... ({elapsed_str})"
                )
            elif status_val == "IN_PROGRESS":
                pct = min(0.2 + (elapsed / TIMEOUT) * 0.6, 0.8)
                progress_bar.progress(pct)
                progress_text.markdown(
                    f"**IN_PROGRESS** — Generating images... ({elapsed_str})"
                )
            elif status_val == "COMPLETED":
                progress_bar.progress(1.0)
                progress_text.markdown(f"**COMPLETED** ({elapsed_str})")
                break
            elif status_val == "FAILED":
                error_detail = result.get("error", "Unknown error")
                st.error(f"Job failed: {error_detail}")
                status_container.update(label="Job failed", state="error")
                st.stop()
            else:
                progress_text.markdown(
                    f"**{status_val}** ({elapsed_str})"
                )

        status_container.update(
            label=f"Completed in {int(elapsed)}s", state="complete"
        )

    # Store results
    output = result.get("output", {})
    if "error" in output:
        st.error(f"Worker error: {output['error']}")
        st.stop()

    st.session_state["last_result"] = output
    st.session_state["last_output_dir"] = output_dir
    st.session_state["last_vibe_name"] = safe_vibe

# ── Results gallery ──────────────────────────────────────────────────────────

if "last_result" in st.session_state:
    output = st.session_state["last_result"]
    out_dir = st.session_state["last_output_dir"]
    safe_vibe = st.session_state["last_vibe_name"]

    st.divider()

    # Summary metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Vibe", output.get("vibe_name", safe_vibe))
    c2.metric("Total Images", output.get("total_images", 0))
    c3.metric("Saved To", f"{out_dir}/{safe_vibe}")

    # Warnings
    warnings = output.get("warnings", [])
    if warnings:
        with st.expander(f"Warnings ({len(warnings)})"):
            for w in warnings:
                st.warning(w)

    # Save, extract, and display
    zip_b64 = output.get("zip_base64")
    if not zip_b64:
        st.error("No zip_base64 in response!")
        st.stop()

    zip_path, extract_dir, err = decode_and_save_zip(zip_b64, safe_vibe, out_dir)
    if err:
        st.error(f"Failed to save/extract: {err}")
        st.stop()

    st.success(f"Assets saved to `{extract_dir}`")

    # Download button
    st.download_button(
        label="Download ZIP",
        data=base64.b64decode(zip_b64),
        file_name=f"{safe_vibe}.zip",
        mime="application/zip",
        use_container_width=True,
    )

    # Tabbed image gallery
    st.subheader("Generated Assets")
    tabs = st.tabs(["Backgrounds", "Female", "Male"])

    for tab, category in zip(tabs, CATEGORIES):
        with tab:
            cat_dir = Path(extract_dir) / category
            if not cat_dir.exists():
                st.info(f"No {category}/ folder found")
                continue

            images = sorted(cat_dir.glob("*.png"))
            if not images:
                st.info(f"No images in {category}/")
                continue

            cols = st.columns(min(len(images), 3))
            for idx, img_path in enumerate(images):
                with cols[idx % 3]:
                    img = Image.open(img_path)
                    st.image(img, caption=img_path.name, use_container_width=True)
