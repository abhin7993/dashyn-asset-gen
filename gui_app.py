"""
DashynAssetGen GUI — Multi-vibe streaming with Excel upload.

Run with:
    streamlit run gui_app.py
"""

import base64
import os
import time
from pathlib import Path

import requests
import streamlit as st
from PIL import Image

POLL_INTERVAL = 3  # seconds between /stream polls
BASE_TIMEOUT = 300  # 5 min base (cold start + prompt generation)
PER_IMAGE_TIMEOUT = 60  # ~1 min per image
CATEGORIES = ["backgrounds", "female", "male"]

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DashynAssetGen",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("DashynAssetGen")
st.caption("Generate themed image asset packs via RunPod serverless")

# ── Session state defaults ────────────────────────────────────────────────────

if "api_key" not in st.session_state:
    st.session_state["api_key"] = os.environ.get("RUNPOD_API_KEY", "")
if "endpoint_id" not in st.session_state:
    st.session_state["endpoint_id"] = os.environ.get("RUNPOD_ENDPOINT_ID", "")
if "num_vibes" not in st.session_state:
    st.session_state["num_vibes"] = 1
if "gallery_vibes" not in st.session_state:
    st.session_state["gallery_vibes"] = []  # list of vibe names to display

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("API Configuration")

    if st.session_state["api_key"] and st.session_state["endpoint_id"]:
        st.success("Credentials saved")
        st.text(f"Endpoint: {st.session_state['endpoint_id']}")
        if st.button("Edit Credentials"):
            st.session_state["edit_creds"] = True
            st.rerun()

    if (
        not st.session_state["api_key"]
        or not st.session_state["endpoint_id"]
        or st.session_state.get("edit_creds")
    ):
        new_key = st.text_input(
            "RunPod API Key",
            value=st.session_state["api_key"],
            type="password",
            help="Enter once — saved for the session",
        )
        new_endpoint = st.text_input(
            "RunPod Endpoint ID",
            value=st.session_state["endpoint_id"],
            help="Enter once — saved for the session",
        )
        if st.button("Save Credentials", use_container_width=True, type="primary"):
            st.session_state["api_key"] = new_key
            st.session_state["endpoint_id"] = new_endpoint
            st.session_state.pop("edit_creds", None)
            st.rerun()

    api_key = st.session_state["api_key"]
    endpoint_id = st.session_state["endpoint_id"]

    st.divider()
    st.header("Output Settings")
    output_dir = st.text_input(
        "Output Directory",
        value="./output",
        help="Where to save generated assets",
    )

    st.divider()
    if st.button("Clear Results", use_container_width=True):
        st.session_state["gallery_vibes"] = []
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


def poll_stream(api_key, endpoint_id, job_id):
    """GET /stream/{job_id} — returns (chunks_list, status, error).

    Chunks are consumed on read (not cumulative). Each call returns only
    new chunks since the last poll.
    """
    url = f"https://api.runpod.ai/v2/{endpoint_id}/stream/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return [], None, f"HTTP {r.status_code}"
        data = r.json()
        chunks = [c["output"] for c in data.get("stream", [])]
        return chunks, data.get("status"), None
    except Exception as e:
        return [], None, str(e)


def _next_filename(directory, prefix, ext):
    """Find next available numbered filename (e.g. bg_3.jpg if bg_1, bg_2 exist)."""
    existing = set(p.name for p in directory.glob(f"{prefix}_*{ext}"))
    n = 1
    while f"{prefix}_{n}{ext}" in existing:
        n += 1
    return f"{prefix}_{n}{ext}"


def save_streamed_image(img_bytes, category, vibe_name, output_dir):
    """Save image bytes to output/{vibe_name}/{category}/ with auto-numbering."""
    cat_dir = Path(output_dir) / vibe_name / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    prefix = "bg" if category == "backgrounds" else category
    filename = _next_filename(cat_dir, prefix, ".jpg")
    filepath = cat_dir / filename
    filepath.write_bytes(img_bytes)
    return str(filepath), filename


# ── Excel upload ─────────────────────────────────────────────────────────────

uploaded_file = st.file_uploader(
    "Upload Vibes from Excel",
    type=["xlsx", "xls"],
    help="3 columns, no headers: vibe_name, vibe_description, num_assets",
)

if uploaded_file is not None:
    if st.button("Import from Excel", use_container_width=True):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(uploaded_file)
            ws = wb.active
            vibes = []
            for row in ws.iter_rows(values_only=True):
                if row[0]:
                    vibes.append(
                        {
                            "name": str(row[0]).strip(),
                            "description": str(row[1] or "").strip(),
                            "num_assets": int(row[2]) if row[2] else 2,
                        }
                    )
            if vibes:
                # Clear old vibe keys
                for i in range(st.session_state["num_vibes"]):
                    for k in [f"vibe_name_{i}", f"vibe_desc_{i}", f"vibe_num_{i}"]:
                        st.session_state.pop(k, None)
                # Set new vibes
                st.session_state["num_vibes"] = len(vibes)
                for i, v in enumerate(vibes):
                    st.session_state[f"vibe_name_{i}"] = v["name"]
                    st.session_state[f"vibe_desc_{i}"] = v["description"]
                    st.session_state[f"vibe_num_{i}"] = v["num_assets"]
                st.success(f"Imported {len(vibes)} vibe(s) from Excel")
                st.rerun()
            else:
                st.warning("No valid rows found in Excel")
        except Exception as e:
            st.error(f"Failed to parse Excel: {e}")

# ── Vibe input ───────────────────────────────────────────────────────────────

st.subheader("Vibes")

for i in range(st.session_state["num_vibes"]):
    with st.container(border=True):
        st.markdown(f"**Vibe {i + 1}**")
        c1, c2, c3 = st.columns([2, 4, 1])
        with c1:
            st.text_input(
                "Name",
                placeholder="e.g., mughal_royale",
                key=f"vibe_name_{i}",
                label_visibility="collapsed" if i > 0 else "visible",
            )
        with c2:
            st.text_area(
                "Description",
                placeholder="Detailed aesthetic description...",
                key=f"vibe_desc_{i}",
                height=68,
                label_visibility="collapsed" if i > 0 else "visible",
            )
        with c3:
            st.number_input(
                "Assets",
                min_value=1,
                max_value=10,
                value=2,
                key=f"vibe_num_{i}",
                label_visibility="collapsed" if i > 0 else "visible",
            )

c_add, c_remove, c_summary = st.columns([1, 1, 2])
with c_add:
    if st.button("+ Add Vibe", use_container_width=True):
        st.session_state["num_vibes"] += 1
        st.rerun()
with c_remove:
    if (
        st.button("- Remove Last", use_container_width=True)
        and st.session_state["num_vibes"] > 1
    ):
        n = st.session_state["num_vibes"] - 1
        for k in [f"vibe_name_{n}", f"vibe_desc_{n}", f"vibe_num_{n}"]:
            st.session_state.pop(k, None)
        st.session_state["num_vibes"] = n
        st.rerun()
with c_summary:
    total_images = sum(
        st.session_state.get(f"vibe_num_{i}", 2) * 3
        for i in range(st.session_state["num_vibes"])
    )
    st.metric("Total Images", total_images)

# ── Generate ─────────────────────────────────────────────────────────────────

if st.button("Generate All", type="primary", use_container_width=True):
    # Collect vibes from widget state
    vibes = []
    for i in range(st.session_state["num_vibes"]):
        name = st.session_state.get(f"vibe_name_{i}", "").strip().replace(" ", "_")
        desc = st.session_state.get(f"vibe_desc_{i}", "").strip()
        num = st.session_state.get(f"vibe_num_{i}", 2)
        vibes.append({"name": name, "description": desc, "num_assets": num})

    # Validate
    errors = []
    if not api_key:
        errors.append("RunPod API Key is required (set in sidebar)")
    if not endpoint_id:
        errors.append("RunPod Endpoint ID is required (set in sidebar)")
    for i, v in enumerate(vibes):
        if not v["name"]:
            errors.append(f"Vibe {i + 1}: Name is required")
        if not v["description"]:
            errors.append(f"Vibe {i + 1}: Description is required")
    if errors:
        for e in errors:
            st.error(e)
        st.stop()

    total_images = sum(v["num_assets"] * 3 for v in vibes)
    timeout = BASE_TIMEOUT + total_images * PER_IMAGE_TIMEOUT

    with st.status(
        f"Generating {total_images} images across {len(vibes)} vibe(s)...",
        expanded=True,
    ) as status_container:
        # ── Submit all jobs ──
        jobs = {}  # vibe_name -> {job_id, received, total, done}
        for v in vibes:
            job_id, err = submit_job(
                api_key, endpoint_id, v["name"], v["description"], v["num_assets"]
            )
            if err:
                st.error(f"Failed to submit '{v['name']}': {err}")
                continue
            jobs[v["name"]] = {
                "job_id": job_id,
                "received": 0,
                "total": None,
                "done": False,
            }
            st.write(f"Submitted **{v['name']}** — `{job_id}`")

        if not jobs:
            status_container.update(label="All submissions failed", state="error")
            st.stop()

        # ── Progress UI ──
        progress_bars = {}
        progress_texts = {}
        for vibe_name in jobs:
            progress_texts[vibe_name] = st.empty()
            progress_bars[vibe_name] = st.progress(0)

        latest_img_container = st.empty()
        st.caption(f"Timeout: {timeout // 60} min | Polling every {POLL_INTERVAL}s")

        start_time = time.time()
        generated_vibes = list(jobs.keys())

        # ── Streaming poll loop ──
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                st.error(f"Timeout after {timeout // 60} minutes!")
                status_container.update(label="Timed out", state="error")
                break

            time.sleep(POLL_INTERVAL)

            all_done = True
            for vibe_name, job_info in jobs.items():
                if job_info["done"]:
                    continue
                all_done = False

                chunks, stream_status, err = poll_stream(
                    api_key, endpoint_id, job_info["job_id"]
                )

                if err:
                    progress_texts[vibe_name].markdown(
                        f"**{vibe_name}**: Poll error (retrying) — {err}"
                    )
                    continue

                # Process new chunks
                for chunk in chunks:
                    chunk_type = chunk.get("type")

                    if chunk_type == "progress":
                        job_info["total"] = chunk.get("total_images")
                        progress_texts[vibe_name].markdown(
                            f"**{vibe_name}**: Prompts ready — {job_info['total']} images queued"
                        )

                    elif chunk_type == "image":
                        img_bytes = base64.b64decode(chunk["image_base64"])
                        category = chunk["category"]

                        _, saved_name = save_streamed_image(
                            img_bytes, category, vibe_name, output_dir
                        )

                        job_info["received"] += 1
                        total = job_info.get("total") or chunk.get("total", 1)
                        pct = min(job_info["received"] / total, 1.0)
                        progress_bars[vibe_name].progress(pct)
                        progress_texts[vibe_name].markdown(
                            f"**{vibe_name}**: {job_info['received']}/{total} — "
                            f"saved `{category}/{saved_name}`"
                        )

                        # Show latest image thumbnail
                        latest_img_container.image(
                            img_bytes,
                            caption=f"{vibe_name}/{category}/{saved_name}",
                            width=200,
                        )

                    elif chunk_type == "complete":
                        job_info["done"] = True
                        total = chunk.get("total_images", job_info["received"])
                        progress_bars[vibe_name].progress(1.0)
                        progress_texts[vibe_name].markdown(
                            f"**{vibe_name}**: Complete — {total} images"
                        )
                        warnings = chunk.get("warnings", [])
                        for w in warnings:
                            st.warning(f"{vibe_name}: {w}")

                    elif chunk_type == "error":
                        job_info["done"] = True
                        st.error(f"{vibe_name}: {chunk.get('error', 'Unknown error')}")
                        progress_bars[vibe_name].progress(1.0)

                # Fallback: check stream status if no complete chunk received
                if stream_status in ("COMPLETED", "FAILED") and not job_info["done"]:
                    job_info["done"] = True
                    if stream_status == "FAILED":
                        st.error(f"{vibe_name}: Job failed")
                    progress_bars[vibe_name].progress(1.0)

            if all_done:
                break

        elapsed_final = int(time.time() - start_time)
        total_received = sum(j["received"] for j in jobs.values())
        status_container.update(
            label=f"Done — {total_received} images in {elapsed_final}s",
            state="complete",
        )

    # Store for gallery
    st.session_state["gallery_vibes"] = generated_vibes
    st.session_state["gallery_output_dir"] = output_dir

# ── Results gallery ──────────────────────────────────────────────────────────


def _render_category_gallery(cat_dir):
    """Display images from a category directory in a 3-column grid."""
    if not cat_dir.exists():
        st.info(f"No {cat_dir.name}/ folder found")
        return
    images = sorted(cat_dir.glob("*.jpg")) + sorted(cat_dir.glob("*.png"))
    if not images:
        st.info(f"No images in {cat_dir.name}/")
        return
    cols = st.columns(min(len(images), 3))
    for idx, img_path in enumerate(images):
        with cols[idx % 3]:
            st.image(
                Image.open(img_path),
                caption=img_path.name,
                use_container_width=True,
            )


if st.session_state.get("gallery_vibes"):
    vibe_names = st.session_state["gallery_vibes"]
    out_dir = st.session_state.get("gallery_output_dir", output_dir)

    st.divider()
    st.subheader("Generated Assets")

    if len(vibe_names) == 1:
        # Single vibe — category tabs only
        vibe_name = vibe_names[0]
        st.caption(f"Saved to: `{out_dir}/{vibe_name}/`")

        tabs = st.tabs(["Backgrounds", "Female", "Male"])
        for tab, category in zip(tabs, CATEGORIES):
            with tab:
                _render_category_gallery(Path(out_dir) / vibe_name / category)
    else:
        # Multiple vibes — outer vibe tabs, inner category tabs
        vibe_tabs = st.tabs(vibe_names)
        for vibe_tab, vibe_name in zip(vibe_tabs, vibe_names):
            with vibe_tab:
                st.caption(f"Saved to: `{out_dir}/{vibe_name}/`")

                cat_tabs = st.tabs(["Backgrounds", "Female", "Male"])
                for cat_tab, category in zip(cat_tabs, CATEGORIES):
                    with cat_tab:
                        _render_category_gallery(
                            Path(out_dir) / vibe_name / category
                        )
