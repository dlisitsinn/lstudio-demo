#!/bin/bash
# Mac: дважды щёлкните этот файл.
cd "$(dirname "$0")"
python3 updater.py
python3 main.py "$@"
