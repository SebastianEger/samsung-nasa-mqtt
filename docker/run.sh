#!/usr/bin/bash
docker run --restart unless-stopped --name samsung-nasa-mqtt \
    -d --network host samsung-nasa-mqtt:latest \
    --mqtt-host 192.168.178.2 \
    --serial-port 26 \
    --serial-host 192.168.178.25 \
    --mqtt-port 1883 \
    --nasa-interval 1 \
    --nasa-timeout 60 \
    --nasa-pnp
