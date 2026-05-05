#!/usr/bin/env bash
# Install a udev rule for the PCsensor FootSwitch so it is accessible without sudo.
# Run once: sudo bash scripts/install_footpedal_udev.sh
# Then unplug and replug the footpedal.

set -euo pipefail

RULE_FILE=/etc/udev/rules.d/99-footpedal.rules
NAME_HINT="PCsensor FootSwitch Keyboard"
TARGET_USER="${SUDO_USER:-$USER}"

# Find the matching event device via /sys (no device-open permission needed)
SYS_DEV=""
for dir in /sys/class/input/event*/; do
    name_file="$dir/device/name"
    if [[ -f "$name_file" ]] && grep -qi "$NAME_HINT" "$name_file"; then
        SYS_DEV="$dir/device"
        break
    fi
done

if [[ -z "$SYS_DEV" ]]; then
    echo "Error: no device matching '$NAME_HINT' found."
    echo "Check 'python scripts/test_footpedal.py --list' for available devices."
    exit 1
fi

VENDOR=$(cat "$SYS_DEV/id/vendor")
PRODUCT=$(cat "$SYS_DEV/id/product")
DEV_NAME=$(cat "$SYS_DEV/name")

echo "Device  : $DEV_NAME"
echo "Vendor  : $VENDOR  Product : $PRODUCT"

cat > "$RULE_FILE" <<EOF
# $DEV_NAME
SUBSYSTEM=="input", ATTRS{idVendor}=="$VENDOR", ATTRS{idProduct}=="$PRODUCT", GROUP="input", MODE="0660"
EOF

echo "Written : $RULE_FILE"

udevadm control --reload-rules
udevadm trigger

if id "$TARGET_USER" >/dev/null 2>&1; then
    if ! id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx input; then
        usermod -a -G input "$TARGET_USER"
        echo "Added $TARGET_USER to the 'input' group."
        echo "Log out and back in (or reboot) for the group change to take effect."
    else
        echo "$TARGET_USER is already in the 'input' group."
    fi
fi

echo ""
echo "Done. Unplug and replug the footpedal — then run without sudo:"
echo "  python scripts/test_footpedal.py"
