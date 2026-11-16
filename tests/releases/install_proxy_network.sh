#!/bin/bash
# install.sh under the network a user in mainland China actually has.
#
# A squid proxy sits on two docker networks: `vs-open` (normal internet)
# and `vs-blocked` (--internal: no route out at all). A container on
# vs-blocked can reach the internet ONLY through the proxy — the "the
# direct network does not work, the proxy does" case. The proxy is
# announced the way the OS announces it: a stand-in `scutil` prints the
# macOS system-proxy dictionary, which is all install.sh reads.
#
#   blocked-proxy-alive   direct fails, proxy works → the FULL default
#                         install (uv, Node, Claude Code, vibe-seller,
#                         Chromium + apt deps) completes through it, and
#                         the proxy's log shows the traffic.
#   open-proxy-dead       direct works, proxy dead → warned, ignored,
#                         downloads go direct.
#   open-proxy-alive      both work → the proxy is used.
#   blocked-proxy-dead    neither works → warned, nothing exported.
#
#   tests/releases/install_proxy_network.sh [case ...]   (default: all)
#
# Needs docker. Cleans up its networks and containers on exit.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PROXY=vs-proxy
IMAGE=vs-install-test
CASES=("$@")
[ ${#CASES[@]} -gt 0 ] || CASES=(
    open-proxy-dead open-proxy-alive blocked-proxy-dead blocked-proxy-alive
)

cleanup() {
    docker rm -f "$PROXY" >/dev/null 2>&1 || true
    docker network rm vs-blocked vs-open >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

# A desktop-like Ubuntu: curl, git and sudo present, nothing else.
# Built while the network is open; the install itself runs later.
docker build -q -t "$IMAGE" - >/dev/null <<'EOF'
FROM buildpack-deps:noble-scm
RUN apt-get update -qq && apt-get install -y -qq sudo >/dev/null \
    && rm -rf /var/lib/apt/lists/*
EOF

docker network create vs-open >/dev/null
docker network create --internal vs-blocked >/dev/null
docker run -d --name "$PROXY" --network vs-open ubuntu/squid:latest >/dev/null
docker network connect vs-blocked "$PROXY"

# The stand-in scutil: macOS's system-proxy dictionary for $FAKE_PROXY
# (host:port), or "all off" when it is unset.
FAKEBIN="$(mktemp -d)"
cat > "$FAKEBIN/scutil" <<'EOF'
#!/bin/sh
if [ -z "$FAKE_PROXY" ]; then
  printf '<dictionary> {\n  HTTPEnable : 0\n}\n'; exit 0
fi
h=${FAKE_PROXY%:*}; p=${FAKE_PROXY##*:}
printf '<dictionary> {\n  HTTPEnable : 1\n  HTTPProxy : %s\n  HTTPPort : %s\n  HTTPSEnable : 1\n  HTTPSProxy : %s\n  HTTPSPort : %s\n}\n' "$h" "$p" "$h" "$p"
EOF
chmod +x "$FAKEBIN/scutil"

# run <network> <fake proxy host:port or ''> <bash script>
run() {
    docker run --rm --network "$1" \
        -e FAKE_PROXY="$2" \
        -v "$REPO:/repo:ro" -v "$FAKEBIN:/fakebin:ro" \
        "$IMAGE" bash -c "export PATH=/fakebin:\$PATH; $3"
}

echo "== waiting for the proxy"
for i in $(seq 1 30); do
    if run vs-blocked '' "curl -fsS -o /dev/null --max-time 5 \
        -x http://$PROXY:3128 https://github.com"; then
        break
    fi
    [ "$i" = 30 ] && { echo "proxy never came up"; docker logs "$PROXY"; exit 1; }
    sleep 2
done

LIVE="$PROXY:3128"
DEAD="127.0.0.1:9"
# setup_proxy alone, then the env it left behind.
PROBE='VIBE_SELLER_INSTALL_SH_NO_MAIN=1 source /repo/install.sh
setup_proxy
echo "https_proxy=[${https_proxy:-}]"'

# grep runs on the file inside the proxy container: no pipe. Piping
# `docker exec … cat` into `grep -q` under pipefail answered "absent"
# for lines that were there — grep -q exits on the first match, the
# writer dies of SIGPIPE (141), and pipefail reports the pipeline's
# failure, more often the earlier the match sits in the log.
proxy_log_has() {
    docker exec "$PROXY" grep -qF "$1" /var/log/squid/access.log
}

fail=0
check() {  # check <case> <condition description> <command...>
    local c="$1" what="$2"; shift 2
    if "$@"; then echo "  ok   $what"; else echo "  FAIL $what"; fail=1; fi
}

for c in "${CASES[@]}"; do
    echo "== $c"
    case "$c" in
    open-proxy-dead)
        out="$(run vs-open "$DEAD" "$PROBE
            curl -fsS -o /dev/null --max-time 20 https://github.com && echo DIRECT_OK" 2>&1)" || true
        check "$c" 'warned the proxy is dead' grep -q 'not answering' <<<"$out"
        check "$c" 'exported nothing' grep -q 'https_proxy=\[\]' <<<"$out"
        check "$c" 'direct download works' grep -q DIRECT_OK <<<"$out"
        ;;
    open-proxy-alive)
        out="$(run vs-open "$LIVE" "$PROBE
            curl -fsS -o /dev/null --max-time 20 https://pypi.org/simple/ && echo VIA_OK" 2>&1)" || true
        check "$c" 'proxy exported' grep -q "https_proxy=\[http://$LIVE\]" <<<"$out"
        check "$c" 'download works' grep -q VIA_OK <<<"$out"
        check "$c" 'traffic went through the proxy' proxy_log_has 'pypi.org:443'
        ;;
    blocked-proxy-dead)
        out="$(run vs-blocked "$DEAD" "$PROBE" 2>&1)" || true
        check "$c" 'warned the proxy is dead' grep -q 'not answering' <<<"$out"
        check "$c" 'exported nothing' grep -q 'https_proxy=\[\]' <<<"$out"
        ;;
    blocked-proxy-alive)
        # Precondition: this container really has no direct route.
        if run vs-blocked '' 'curl -fsS -o /dev/null --max-time 5 https://github.com' \
            >/dev/null 2>&1; then
            echo "  FAIL precondition: vs-blocked reaches the internet directly"
            fail=1
            continue
        fi
        echo "  ok   direct network is blocked"
        log="$(mktemp)"
        if run vs-blocked "$LIVE" 'bash /repo/install.sh \
            && ~/.local/bin/vibe-seller --version' >"$log" 2>&1; then
            echo "  ok   full install completed through the proxy"
        else
            echo "  FAIL full install did not complete — last 60 lines:"
            tail -60 "$log" | sed 's/^/       /'
            fail=1
        fi
        check "$c" 'install announced the system proxy' \
            grep -q 'Using your system proxy' "$log"
        for host in github.com pypi.org registry.npmjs.org; do
            check "$c" "$host fetched through the proxy" proxy_log_has "$host:443"
        done
        ;;
    *) echo "unknown case: $c"; fail=1 ;;
    esac
done
exit "$fail"
