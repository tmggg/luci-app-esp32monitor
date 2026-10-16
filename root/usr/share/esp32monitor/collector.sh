#!/bin/sh
# Sourced by the single collector; state survives between rounds.
export LC_ALL=C
set -f
. /lib/functions.sh
monotonic_ms() { awk '{ printf "%.0f", $1 * 1000 }' /proc/uptime; }
valid_device() {
    case "$1" in ''|*[!A-Za-z0-9_.:@-]*|.|..) return 1 ;; esac
    return 0
}

read_cpu() {
    set -- $(awk '/^cpu / && NF >= 9 { for(i=2;i<=9;i++) if($i !~ /^[0-9]+$/) exit; printf "%.0f %.0f\n", $5+$6, $2+$3+$4+$5+$6+$7+$8+$9; exit }' /proc/stat)
    CPU_IDLE=${1:-null}
    CPU_TOTAL=${2:-null}
}

detect_wan_device() {
    local dev
    if valid_device "$ESP32_STATUS_IFACE" && [ -d "/sys/class/net/$ESP32_STATUS_IFACE" ]; then
        printf '%s' "$ESP32_STATUS_IFACE"
        return
    fi
    if command -v ubus >/dev/null 2>&1 && command -v jsonfilter >/dev/null 2>&1; then
        dev="$(ubus call network.interface.wan status 2>/dev/null | jsonfilter -e '@.l3_device' 2>/dev/null)"
        [ -n "$dev" ] && [ -d "/sys/class/net/$dev" ] && { printf '%s' "$dev"; return; }
    fi
    dev="$(ip route show default 2>/dev/null | awk 'NR == 1 { for (i=1; i<=NF; i++) if ($i == "dev") { print $(i+1); exit } }')"
    [ -n "$dev" ] && [ -d "/sys/class/net/$dev" ] && { printf '%s' "$dev"; return; }
    printf 'unknown'
}

read_counter() {
    local file="/sys/class/net/$1/statistics/$2"
    local value
    value="$(sed -n '1p' "$file" 2>/dev/null)"
    case "$value" in ''|*[!0-9]*) printf 'null' ;; *) printf '%s' "$value" ;; esac
}

is_link_up() {
    local dev="$1" carrier_file state_file value
    carrier_file="/sys/class/net/$dev/carrier"
    state_file="/sys/class/net/$dev/operstate"

    # Physical and most virtual netdevs expose carrier: 1 means that the
    # interface currently has an active lower-layer link.
    if [ -r "$carrier_file" ]; then
        value="$(sed -n '1p' "$carrier_file" 2>/dev/null)"
        case "$value" in
            1) return 0 ;;
            0) return 1 ;;
        esac
    fi

    # Fall back for netdev types that do not implement the carrier attribute.
    [ -r "$state_file" ] || return 1
    value="$(sed -n '1p' "$state_file" 2>/dev/null)"
    [ "$value" = "up" ]
}

write_network_snapshot() {
    local output="$1" path dev rx tx idx idx_after stamp
    : > "$output" || return 1
    set +f
    for path in /sys/class/net/*; do
        [ -d "$path" ] || continue
        dev="${path##*/}"
        [ "$dev" = "lo" ] && continue
        valid_device "$dev" || continue
        if [ -n "$INTERFACES" ]; then
            case " $INTERFACES " in *" $dev "*) ;; *) continue ;; esac
        fi
        if [ "$ONLY_LINK_UP" = "1" ]; then
            is_link_up "$dev" || continue
        fi
        idx="$(cat "$path/ifindex" 2>/dev/null)"
        stamp="$(monotonic_ms)"
        rx="$(read_counter "$dev" rx_bytes)"
        tx="$(read_counter "$dev" tx_bytes)"
        idx_after="$(cat "$path/ifindex" 2>/dev/null)"
        case "$idx" in ''|*[!0-9]*) idx=null ;; esac
        [ "$idx" = "$idx_after" ] || { idx=null; rx=null; tx=null; }
        printf '%s %s %s %s %s\n' "$dev" "$rx" "$tx" "$idx" "$stamp" >> "$output" || return 1
    done
    set -f
}

read_temperature() {
    local file value
    set +f
    for file in /sys/class/thermal/thermal_zone*/temp /sys/class/hwmon/hwmon*/temp*_input; do
        [ "$TEMPERATURE_SOURCE" = "auto" ] || [ -z "$TEMPERATURE_SOURCE" ] || [ "$TEMPERATURE_SOURCE" = "$file" ] || continue
        [ -r "$file" ] || continue
        value="$(sed -n '1p' "$file" 2>/dev/null)"
        # Both Linux thermal and hwmon temperature attributes use millidegrees C.
        if printf '%s\n' "$value" | awk '/^-?[0-9]+$/ { found=1 } END { exit !found }'; then
            awk -v value="$value" 'BEGIN { printf "%.1f", value / 1000 }'
            return
        fi
    done
    printf 'null'
}


sample_once() {
    config_load esp32monitor
    config_get ENABLED main enabled 0
    config_get ESP32_STATUS_IFACE main wan_device ''
    config_get INTERFACES main interfaces ''
    config_get ONLY_LINK_UP main only_link_up 1
    config_get TEMPERATURE_SOURCE main temperature_source auto
    config_get S3_GIF_ENABLED main s3_gif_enabled 0
    config_get SCREENSAVER_TYPE main screensaver_type clock
    case "$SCREENSAVER_TYPE" in clock|gif) ;; *) SCREENSAVER_TYPE=clock ;; esac
    [ "$S3_GIF_ENABLED" = "1" ] || SCREENSAVER_TYPE=clock
    config_get SCREENSAVER_TIMEOUT main screensaver_timeout 60
    # Normalize direct UCI edits too, keeping the JSON value a valid integer.
    SCREENSAVER_TIMEOUT="$(printf '%s\n' "$SCREENSAVER_TIMEOUT" | awk '
        NR == 1 && /^[0-9]+$/ && $0+0 <= 86400 { value=$0+0; valid=1 }
        END { if (NR == 1 && valid) printf "%.0f", value; else printf "60" }
    ')"


    [ "$ENABLED" = 1 ] || return 1
    HOSTNAME_VALUE="$(cat /proc/sys/kernel/hostname | tr -cd 'A-Za-z0-9._-')"
    [ -n "$HOSTNAME_VALUE" ] || HOSTNAME_VALUE=OpenWrt
    WAN_DEVICE="$(detect_wan_device)"
    ESCAPED_WAN_DEVICE="$(printf '%s' "$WAN_DEVICE" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    write_network_snapshot "$SNAPSHOT_2" || return 1
    read_cpu
    SAMPLE_END="$(monotonic_ms)" || return 1
    case "$SAMPLE_END" in ''|*[!0-9]*) return 1 ;; esac
    INTERVAL_MS="$(awk -v old="${SAMPLE_PREVIOUS:-}" -v now="$SAMPLE_END" 'BEGIN { if (old != "" && now > old) printf "%.0f", now-old; else printf "null" }')"
    CPU_PERCENT="$(awk -v t1="${CPU_TOTAL_1:-null}" -v t2="$CPU_TOTAL" -v i1="${CPU_IDLE_1:-null}" -v i2="$CPU_IDLE" 'BEGIN { dt=t2-t1; di=i2-i1; if (t1 != "null" && t2 != "null" && dt>0 && di>=0 && di<=dt) printf "%.1f", 100*(dt-di)/dt; else printf "null" }')"
    MEM_TOTAL="$(awk '/^MemTotal:/ { print $2; exit }' /proc/meminfo)"
    MEM_AVAILABLE="$(awk '/^MemAvailable:/ { print $2; exit }' /proc/meminfo)"
    if [ -z "$MEM_AVAILABLE" ]; then
        MEM_AVAILABLE="$(awk '/^MemFree:/ { f=$2 } /^Buffers:/ { b=$2 } /^Cached:/ { c=$2 } END { if(f != "" && b != "" && c != "") print f+b+c }' /proc/meminfo)"
    fi
    MEM_PERCENT="$(awk -v total="${MEM_TOTAL:-0}" -v avail="${MEM_AVAILABLE:-null}" 'BEGIN { if (total > 0 && avail != "null" && avail >= 0 && avail <= total) printf "%.1f", 100*(total-avail)/total; else printf "null" }')"
    TEMPERATURE="$(read_temperature)"
    UPTIME="$(awk '{ printf "%.0f", int($1) }' /proc/uptime)"

    # One clock read keeps the date and time consistent across midnight.
    SYSTEM_DATETIME="$(date '+%Y-%m-%d %H:%M:%S')"
    SYSTEM_DATE="${SYSTEM_DATETIME%% *}"
    SYSTEM_TIME="${SYSTEM_DATETIME#* }"

    GIF_SIZE=0
    GIF_VERSION=""
    if [ "$SCREENSAVER_TYPE" = "gif" ] && [ -r /etc/esp32monitor/screensaver.gif ]; then
        # Hash and size describe the same inode, even while uploading a replacement.
        exec 3< /etc/esp32monitor/screensaver.gif || return 1
        GIF_SIZE="$(wc -c < /proc/self/fd/3 | tr -d ' ')"
        GIF_KEY="$(stat -L -c '%i:%s:%Y:%Z' /proc/self/fd/3 2>/dev/null)"
        if [ -z "$GIF_KEY" ] || [ "$GIF_KEY" != "${LAST_GIF_KEY:-}" ]; then
            LAST_GIF_HASH="$(sha256sum /proc/self/fd/3 | awk '{ print $1 }')"
            LAST_GIF_KEY="$GIF_KEY"
        fi
        GIF_VERSION="$LAST_GIF_HASH"
        exec 3<&-
    fi


    INTERFACE_COUNT="$(wc -l < "$SNAPSHOT_2" | tr -d ' ')"
    SAMPLE_SEQ="$(awk -v n="${SAMPLE_SEQ:-0}" 'BEGIN { printf "%.0f", n+1 }')"
    INTERFACE_JSON="$(awk -f /usr/share/esp32monitor/rates.awk "$SNAPSHOT_1" "$SNAPSHOT_2")" || return 1
    {
    printf '{'
    printf '"ok":true,"schema_version":2,'
    printf '"boot_id":"%s","collector_id":"%s","sample_seq":%s,' "$BOOT_ID" "$COLLECTOR_ID" "$SAMPLE_SEQ"
    printf '"sample_end_ms":%s,"interval_ms":%s,' "$SAMPLE_END" "$INTERVAL_MS"
    printf '"hostname":"%s",' "$HOSTNAME_VALUE"
    printf '"cpu_percent":%s,' "$CPU_PERCENT"
    printf '"memory_percent":%s,' "$MEM_PERCENT"
    printf '"temperature_c":%s,' "$TEMPERATURE"
    printf '"uptime_seconds":%s,' "$UPTIME"
    printf '"system_date":"%s",' "$SYSTEM_DATE"
    printf '"system_time":"%s",' "$SYSTEM_TIME"
    printf '"screensaver_timeout":%s,' "$SCREENSAVER_TIMEOUT"
    printf '"screensaver_type":"%s",' "$SCREENSAVER_TYPE"
    if [ -n "$GIF_VERSION" ]; then
        printf '"gif_url":"/cgi-bin/esp32-gif","gif_version":"%s","gif_size":%s,' "$GIF_VERSION" "$GIF_SIZE"
    else
        printf '"gif_url":null,"gif_version":null,"gif_size":0,'
    fi
    printf '"wan_device":"%s",' "$ESCAPED_WAN_DEVICE"
    printf '"interface_count":%s,' "$INTERFACE_COUNT"
    printf '"interfaces":['

    printf '%s' "$INTERFACE_JSON"
    printf ']}\n'
    } > "$CACHE_DIR/snapshot.new" || return 1
    # Do not publish a partial/invalid document after a read or write failure.
    [ "$(jsonfilter -i "$CACHE_DIR/snapshot.new" -e '@.ok' 2>/dev/null)" = true ] || return 1
    mv -f "$CACHE_DIR/snapshot.new" "$CACHE_DIR/snapshot.json" || return 1
    mv -f "$SNAPSHOT_2" "$SNAPSHOT_1" || return 1
    CPU_IDLE_1=$CPU_IDLE
    CPU_TOTAL_1=$CPU_TOTAL
    SAMPLE_PREVIOUS=$SAMPLE_END
}
