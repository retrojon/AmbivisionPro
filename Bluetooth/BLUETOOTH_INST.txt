#!/bin/bash
set -e

echo "[*] Installing devmem2..."
chmod +x /root/devmem2
cp /root/devmem2 /usr/bin/

echo "[*] Installing BlueZ manually..."
dpkg -i /root/bluez_5.43-2+deb9u1_armhf-fix.deb || true
apt-get install -f -y || true

echo "[*] Adding required kernel modules to /etc/modules..."
grep -qxF 'hci_uart' /etc/modules || echo "hci_uart" >> /etc/modules
grep -qxF 'bluetooth' /etc/modules || echo "bluetooth" >> /etc/modules

echo "[*] Creating /etc/default/ap6212..."
cat >/etc/default/ap6212 <<'EOF'
#
# Default it is called to be uncertain which MAC address the chipset has.
# Therefore it is recommendable to set the MAC address manually.
# If this variable is empty or not set the default 11:22:33:44:55:66 will be chosen.

MAC_ADDR=43:29:B1:55:01:01
PORT=ttyS1
EOF

echo "[*] Creating /etc/init.d/ap6212-bluetooth..."
cat >/etc/init.d/ap6212-bluetooth <<'EOF'
#!/bin/sh
### BEGIN INIT INFO
# Provides:          ap6212-bluetooth
# Required-Start:    $local_fs
# Required-Stop:
# X-Start-Before:    bluetooth
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Patch firmware for ap6212 adapter
# Description:       Patch firmware for ap6212 adapter
### END INIT INFO

DEFAULTS="/etc/default/ap6212"
[ -r "$DEFAULTS" ] && . "$DEFAULTS"

[ -x "/bin/hciconfig" ] || exit 0

if [ -f "/lib/firmware/ap6212/bcm43438a0.hcd" ] && [ ! -f "/etc/firmware/ap6212/4343A0.hcd" ]; then
    mkdir -p /etc/firmware/ap6212
    cp /lib/firmware/ap6212/bcm43438a0.hcd /etc/firmware/ap6212/4343A0.hcd
fi

if [ -f "/lib/firmware/ap6212/bcm43438a1.hcd" ] && [ ! -f "/etc/firmware/ap6212/BCM43430A1.hcd" ]; then
    mkdir -p /etc/firmware/ap6212
    cp /lib/firmware/ap6212/bcm43438a1.hcd /etc/firmware/ap6212/BCM43430A1.hcd
fi

. /lib/lsb/init-functions

do_start () {
    if hciconfig | grep -q UART; then
        echo "ap6212 BT device already initialized"
        hcitool dev
    else
        MAC_OPTIONS="${MAC_ADDR:-11:22:33:44:55:66}"
        PORT="${PORT:-ttyS1}"

        modprobe hci_uart
        modprobe bluetooth
        rfkill unblock all

        devmem2 0x1f00060 b 1

        echo 10 > /sys/class/gpio/export 2>/dev/null || true
        echo out > /sys/class/gpio/gpio10/direction
        echo 0 > /sys/class/gpio/gpio10/value
        sleep 0.1
        echo 1 > /sys/class/gpio/gpio10/value
        sleep 0.5

        timeout 5s echo " " > /dev/$PORT || echo " " > /dev/$PORT
        killall hciattach 2>/dev/null || true
        hciattach /dev/$PORT bcm43xx 115200 flow bdaddr $MAC_OPTIONS &
        sleep 2
        hciconfig hci0 up
    fi
}

do_stop () {
    killall hciattach 2>/dev/null || true
    hciconfig hci0 down 2>/dev/null || true
}

case "$1" in
    start)
        do_start
        ;;
    restart|reload|force-reload)
        do_stop
        do_start
        ;;
    stop)
        do_stop
        ;;
    status)
        hcitool dev
        ;;
    *)
        echo "Usage: ap6212-bluetooth [start|stop|status|restart]" >&2
        exit 3
        ;;
esac
EOF

chmod 755 /etc/init.d/ap6212-bluetooth

echo "[*] Disabling serial-getty@ttyS1.service..."
systemctl stop serial-getty@ttyS1.service || true
systemctl disable serial-getty@ttyS1.service || true

echo "[*] Reloading systemd and enabling Bluetooth services..."
systemctl daemon-reload
systemctl enable ap6212-bluetooth
systemctl enable bluetooth
systemctl start bluetooth
/etc/init.d/ap6212-bluetooth start

echo "[*] Done! Run 'rfkill' and 'hcitool scan' to verify."
