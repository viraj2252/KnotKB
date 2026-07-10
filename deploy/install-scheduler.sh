#!/bin/sh
# Install/uninstall/status for the nightly `kb consolidate --apply` job.
#
#   deploy/install-scheduler.sh install [--dry-run]
#   deploy/install-scheduler.sh uninstall
#   deploy/install-scheduler.sh status [--dry-run]
#
# Platform autodetect: macOS -> launchd, Linux/WSL2 with systemd -> systemd
# user timer, otherwise -> crontab. --dry-run renders the config to stdout
# without touching anything.
set -eu

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNNER="$DEPLOY_DIR/kb-consolidate.sh"
ACTION="${1:-}"
DRY_RUN=0
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1

usage() { echo "usage: $0 install|uninstall|status [--dry-run]" >&2; exit 2; }
[ -n "$ACTION" ] || usage

detect_platform() {
    case "$(uname -s)" in
        Darwin) echo launchd ;;
        Linux)
            if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
                echo systemd
            else
                echo cron
            fi ;;
        *) echo cron ;;
    esac
}

render() { # render <template> <log-prefix>
    sed -e "s|@KB_RUNNER@|$RUNNER|g" -e "s|@KB_LOG@|$2|g" "$1"
}

PLATFORM="$(detect_platform)"
echo "platform: $PLATFORM"

case "$PLATFORM" in
launchd)
    PLIST="$HOME/Library/LaunchAgents/dev.kb.consolidate.plist"
    LOG="$HOME/Library/Logs/kb-consolidate"
    case "$ACTION" in
    install)
        if [ "$DRY_RUN" = 1 ]; then
            render "$DEPLOY_DIR/launchd/dev.kb.consolidate.plist.tmpl" "$LOG"
            exit 0
        fi
        mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
        render "$DEPLOY_DIR/launchd/dev.kb.consolidate.plist.tmpl" "$LOG" > "$PLIST"
        launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
        echo "installed: $PLIST (nightly 03:30, logs at $LOG.{log,err})"
        ;;
    uninstall)
        launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
        echo "removed: $PLIST"
        ;;
    status)
        OUT="$(launchctl print "gui/$(id -u)/dev.kb.consolidate" 2>/dev/null | head -20)"
        [ -n "$OUT" ] && echo "$OUT" || echo "not installed"
        ;;
    *) usage ;;
    esac
    ;;
systemd)
    UNIT_DIR="$HOME/.config/systemd/user"
    LOG="(journal: journalctl --user -u kb-consolidate)"
    case "$ACTION" in
    install)
        if [ "$DRY_RUN" = 1 ]; then
            render "$DEPLOY_DIR/systemd/kb-consolidate.service.tmpl" ""
            cat "$DEPLOY_DIR/systemd/kb-consolidate.timer"
            exit 0
        fi
        mkdir -p "$UNIT_DIR"
        render "$DEPLOY_DIR/systemd/kb-consolidate.service.tmpl" "" > "$UNIT_DIR/kb-consolidate.service"
        cp "$DEPLOY_DIR/systemd/kb-consolidate.timer" "$UNIT_DIR/kb-consolidate.timer"
        systemctl --user daemon-reload
        systemctl --user enable --now kb-consolidate.timer
        echo "installed: kb-consolidate.timer (nightly 03:30, logs in the user journal)"
        echo "note: for runs while logged out, enable lingering: loginctl enable-linger $USER"
        ;;
    uninstall)
        systemctl --user disable --now kb-consolidate.timer 2>/dev/null || true
        rm -f "$UNIT_DIR/kb-consolidate.service" "$UNIT_DIR/kb-consolidate.timer"
        systemctl --user daemon-reload
        echo "removed: kb-consolidate.{service,timer}"
        ;;
    status)
        systemctl --user status kb-consolidate.timer --no-pager 2>/dev/null || echo "not installed"
        ;;
    *) usage ;;
    esac
    ;;
cron)
    LOG_DIR="$HOME/.local/state/kb"
    LOG="$LOG_DIR/consolidate"
    case "$ACTION" in
    install)
        if [ "$DRY_RUN" = 1 ]; then
            render "$DEPLOY_DIR/cron/kb-consolidate.crontab.tmpl" "$LOG"
            exit 0
        fi
        mkdir -p "$LOG_DIR"
        TMP="$(mktemp)"
        { crontab -l 2>/dev/null | sed '/# kb-consolidate BEGIN/,/# kb-consolidate END/d'
          render "$DEPLOY_DIR/cron/kb-consolidate.crontab.tmpl" "$LOG"
        } > "$TMP"
        crontab "$TMP" && rm -f "$TMP"
        echo "installed: crontab entry (nightly 03:30, log at $LOG.log)"
        echo "note: WSL2 without systemd may need the cron service started (sudo service cron start)"
        ;;
    uninstall)
        TMP="$(mktemp)"
        crontab -l 2>/dev/null | sed '/# kb-consolidate BEGIN/,/# kb-consolidate END/d' > "$TMP"
        crontab "$TMP" && rm -f "$TMP"
        echo "removed: crontab entry"
        ;;
    status)
        crontab -l 2>/dev/null | grep -A1 'kb-consolidate BEGIN' || echo "not installed"
        ;;
    *) usage ;;
    esac
    ;;
esac
