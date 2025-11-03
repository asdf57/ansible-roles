#!/usr/bin/env bash

BRIGHTNESS_CODE=10

function help() {
    echo "Usage: $0 <brightness>"
    echo "  <brightness> should be a value between 0 and 100"
    exit 1
}

if [[ "$#" != 1 ]]; then
    exit 1
fi

if ! command -v ddcutil >/dev/null 2>&1; then
    echo "ddcutil is not installed!!!"
    exit 1
fi

ddcutil --display 2 setvcp 10 100
