#!/usr/bin/bash
docker run --restart unless-stopped --name samsung-nasa-mqtt \
    -d --network host samsung-nasa-mqtt:latest \
    --serial-host 192.168.178.25 \
    --serial-port 26 \
    --mqtt-host 192.168.178.2 \
    --mqtt-port 1883 \
    --nasa-pnp
