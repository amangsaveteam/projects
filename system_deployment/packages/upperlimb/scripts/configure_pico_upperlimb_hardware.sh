#!/usr/bin/env bash
# The launcher synchronises /var/opt/hardware_body.yaml from the selected
# uplimb_runtime model configuration on every start.

set -euo pipefail

install -d -m 0755 /var/opt
printf 'Pico upperlimb hardware: the service will synchronise the model-specific runtime file on start.\n'
