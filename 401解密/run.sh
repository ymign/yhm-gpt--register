#!/bin/sh
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt -q
python3 run.py
