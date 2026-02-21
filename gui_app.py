"""
DashynAssetGen GUI — Multi-vibe streaming with Excel upload.

Run with:
    streamlit run gui_app.py
"""

import base64
import json
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
CONFIG_PATH = Path(__file__).parent / ".config.json"


def _load_config():
    """Load saved credentials from local config file."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(api_key, endpoint_id):
    """Persist credentials to local config file."""
    CONFIG_PATH.write_text(json.dumps({"api_key": api_key, "endpoint_id": endpoint_id}))

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
    _cfg = _load_config()
    st.session_state["api_key"] = (
        os.environ.get("RUNPOD_API_KEY") or _cfg.get("api_key", "")
    )
    st.session_state["endpoint_id"] = (
        os.environ.get("RUNPOD_ENDPOINT_ID") or _cfg.get("endpoint_id", "")
    )
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
            st.session_state["api_key"] = new_key.strip()
            st.session_state["endpoint_id"] = new_endpoint.strip()
            _save_config(new_key.strip(), new_endpoint.strip())
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


def submit_run(api_key, endpoint_id, input_payload):
    """POST /run with arbitrary input — returns (job_id, error_message)."""
    url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(url, json={"input": input_payload}, headers=headers, timeout=30)
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
    start_time = time.time()

    # ══════════════════════════════════════════════════════════════════════
    # Phase 1: Generate prompts (one job per vibe — fast, Claude API only)
    # ══════════════════════════════════════════════════════════════════════
    with st.status(
        f"Phase 1 — Generating prompts for {len(vibes)} vibe(s)...",
        expanded=True,
    ) as phase1_status:
        prompt_jobs = {}  # vibe_name -> job_id
        for v in vibes:
            job_id, err = submit_run(api_key, endpoint_id, {
                "mode": "generate_prompts",
                "vibe_name": v["name"],
                "vibe_description": v["description"],
                "num_assets": v["num_assets"],
            })
            if err:
                st.error(f"Failed to submit prompts for '{v['name']}': {err}")
                continue
            prompt_jobs[v["name"]] = job_id
            st.write(f"Submitted **{v['name']}** prompt job — `{job_id}`")

        if not prompt_jobs:
            phase1_status.update(label="All prompt submissions failed", state="error")
            st.stop()

        # Poll for prompt results
        all_prompts = {}  # vibe_name -> {backgrounds: [...], female: [...], male: [...]}
        prompt_progress = st.empty()
        pending_prompts = dict(prompt_jobs)

        while pending_prompts:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                st.error("Timeout waiting for prompts!")
                phase1_status.update(label="Timed out", state="error")
                st.stop()

            time.sleep(POLL_INTERVAL)

            for vibe_name in list(pending_prompts.keys()):
                status_val, result, err = poll_status(
                    api_key, endpoint_id, pending_prompts[vibe_name]
                )
                if err:
                    continue

                if status_val == "COMPLETED":
                    output = result.get("output", [])
                    for chunk in output:
                        if chunk.get("type") == "prompts":
                            all_prompts[vibe_name] = chunk["prompts"]
                        elif chunk.get("type") == "error":
                            st.error(f"{vibe_name}: {chunk['error']}")
                    del pending_prompts[vibe_name]

                elif status_val == "FAILED":
                    st.error(f"{vibe_name}: Prompt generation failed")
                    del pending_prompts[vibe_name]

            prompt_progress.markdown(
                f"Prompts ready: **{len(all_prompts)}/{len(prompt_jobs)}** vibes"
            )

        if not all_prompts:
            phase1_status.update(label="All prompt jobs failed", state="error")
            st.stop()

        phase1_status.update(
            label=f"Prompts ready — {len(all_prompts)} vibe(s)",
            state="complete",
        )

    # ══════════════════════════════════════════════════════════════════════
    # Phase 2: Render images (one job per image — fans out across workers)
    # ══════════════════════════════════════════════════════════════════════

    # Build task list from collected prompts
    render_tasks = []  # [(vibe_name, category, prompt_text)]
    for vibe_name, prompts in all_prompts.items():
        for pt in prompts.get("backgrounds", []):
            render_tasks.append((vibe_name, "backgrounds", pt))
        for pt in prompts.get("female", []):
            render_tasks.append((vibe_name, "female", pt))
        for pt in prompts.get("male", []):
            render_tasks.append((vibe_name, "male", pt))

    total_render = len(render_tasks)

    with st.status(
        f"Phase 2 — Rendering {total_render} images across workers...",
        expanded=True,
    ) as phase2_status:
        # Submit ALL render jobs at once — RunPod distributes across workers
        render_jobs = {}  # job_id -> {vibe_name, category}
        for vibe_name, category, prompt_text in render_tasks:
            job_id, err = submit_run(api_key, endpoint_id, {
                "mode": "render_image",
                "vibe_name": vibe_name,
                "category": category,
                "prompt": prompt_text,
                "width": 576,
                "height": 1024,
            })
            if err:
                st.warning(f"Failed to submit {vibe_name}/{category}: {err}")
                continue
            render_jobs[job_id] = {"vibe_name": vibe_name, "category": category}

        st.write(
            f"Dispatched **{len(render_jobs)}** render jobs "
            f"(RunPod will use all available workers)"
        )

        if not render_jobs:
            phase2_status.update(label="All render submissions failed", state="error")
            st.stop()

        # Progress UI
        progress_text = st.empty()
        progress_bar = st.progress(0)
        latest_img = st.empty()

        completed = 0
        pending_renders = dict(render_jobs)

        while pending_renders:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                st.error(f"Timeout after {timeout // 60} minutes!")
                phase2_status.update(label="Timed out", state="error")
                break

            time.sleep(POLL_INTERVAL)

            for job_id in list(pending_renders.keys()):
                status_val, result, err = poll_status(
                    api_key, endpoint_id, job_id
                )
                if err:
                    continue

                if status_val in ("COMPLETED", "FAILED"):
                    info = pending_renders.pop(job_id)
                    completed += 1

                    if status_val == "COMPLETED":
                        output = result.get("output", [])
                        for chunk in output:
                            if chunk.get("type") == "image":
                                img_bytes = base64.b64decode(chunk["image_base64"])
                                _, saved_name = save_streamed_image(
                                    img_bytes,
                                    info["category"],
                                    info["vibe_name"],
                                    output_dir,
                                )
                                latest_img.image(
                                    img_bytes,
                                    caption=f"{info['vibe_name']}/{info['category']}/{saved_name}",
                                    width=200,
                                )
                            elif chunk.get("type") == "error":
                                st.warning(
                                    f"{info['vibe_name']}/{info['category']}: "
                                    f"{chunk.get('error')}"
                                )
                    else:
                        st.warning(f"{info['vibe_name']}/{info['category']}: Job failed")

                    # Update progress
                    pct = completed / total_render
                    progress_bar.progress(min(pct, 1.0))
                    elapsed_str = f"{int(time.time() - start_time)}s"
                    progress_text.markdown(
                        f"**{int(pct * 100)}%** — "
                        f"{completed}/{total_render} images — "
                        f"{elapsed_str}"
                    )

        elapsed_final = int(time.time() - start_time)
        phase2_status.update(
            label=f"Done — {completed}/{total_render} images in {elapsed_final}s",
            state="complete",
        )

    # Store for gallery
    st.session_state["gallery_vibes"] = list(all_prompts.keys())
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
