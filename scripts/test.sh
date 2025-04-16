#!/usr/bin/bash
python3 ../src/samsung_mqtt_home_assistant_mod.py \
    --serial-host 192.168.178.25 \
    --serial-port 26 \
    --mqtt-host 127.0.0.1 \
    --mqtt-port 1883 \
    --nasa-pnp \
    --nasa-addr 520000