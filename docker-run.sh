#!/usr/bin/bash
docker run --restart unless-stopped \
    -d --network host nasa-mqtt:latest --name samsung-nasa-mqtt \
    --mqtt-host 192.168.178.2 --serial-port 26 --serial-host 192.168.178.25 --mqtt-port 1883 --nasa-timeout 600 --nasa-pnp