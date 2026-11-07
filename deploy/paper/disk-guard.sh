#!/bin/sh
set -eu
minimum_kib=5242880
available_kib=$(df -Pk /var/lib/fastmm/paper | awk 'NR==2 {print $4}')
if [ "$available_kib" -lt "$minimum_kib" ]; then
    touch /var/lib/fastmm/paper/PAUSED
    systemctl stop fastmm-paper-feed.service fastmm-paper-shadow.service
    echo "FastMM paper paused: free disk below 5 GiB. Retained all recorded data."
fi
