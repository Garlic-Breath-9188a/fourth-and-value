#!/bin/sh
# Start Fourth & Value locally. Ctrl-C to stop.
cd "$(dirname "$0")" && exec python3 -m streamlit run app.py
