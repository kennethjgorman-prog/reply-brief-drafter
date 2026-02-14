#!/bin/bash
cd "$(dirname "$0")"
echo "Starting Brief Drafter..."
echo ""
echo "Opening http://127.0.0.1:5003 in your browser..."
sleep 2
open http://127.0.0.1:5003
python3 app.py
