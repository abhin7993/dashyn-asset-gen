#!/bin/bash
# Launch DashynAssetGen GUI
# Usage: ./run_gui.sh

cd "$(dirname "$0")"
python3 -m streamlit run gui_app.py
