"""The vendor private-data guard: every observed spelling, denied.

The runaways (~/adspower-glm/runaways/, scrubbed) are the
specification: each command shape an improvising agent actually ran
must be denied, and every ordinary task command — the corpus — must
pass. The bash_safety-recommended process forms are extracted from
bash_safety's own deny message so the two guards can never drift
apart (D-C3-7).
"""

from pathlib import Path
import re

import pytest

from app.ai import bash_safety
from app.ai.agent_lateral_guard import under_closed_home
from app.ai.output_tripwire import scan_tool_result
from app.ai.vendor_guard import vendor_guard_deny
from app.browser.vendors import BrowserVendorSpec
from app.config import VIBE_SELLER_DIR
import app.plugins as plugins_mod
from app.plugins import ExtensionContext, Plugin, reset_for_tests

pytestmark = pytest.mark.unit

TASK = '1a2b3c4d5e6f7a8b9c0d'
PREFIX = TASK[:8]
VH = str(Path(VIBE_SELLER_DIR).resolve())


class _FakeBackend:
    pass


FAKE_SPEC = BrowserVendorSpec(
    key='guardtest',
    display_name_en='GuardTest',
    display_name_zh='测试浏览器',
    private_paths=(
        # a distinctive bare dir name (wherever the client builds it)
        '.GuardTestData',
        # a home-rooted template with every Windows spelling
        '~/AppData/Roaming/GuardTest Global',
    ),
    local_ports=(59999,),
)


@pytest.fixture
def fake_vendor():
    """Install the fake vendor through the plugin context."""

    class _Plugin(Plugin):
        @property
        def name(self) -> str:
            return 'guardtest'

        def install(self, ctx: ExtensionContext) -> None:
            ctx.register_browser_backend('guardtest', _FakeBackend, FAKE_SPEC)

    class _EntryPoint:
        name = 'fake:guardtest'
        value = 'fake:guardtest'

        def load(self):
            return _Plugin

    orig = plugins_mod.importlib.metadata.entry_points

    def fake_entry_points(group=None):
        return [_EntryPoint()] if group == 'vibe_seller.plugins' else []

    plugins_mod.importlib.metadata.entry_points = fake_entry_points
    reset_for_tests()
    yield
    plugins_mod.importlib.metadata.entry_points = orig
    reset_for_tests()


def bash(cmd, **kw):
    return vendor_guard_deny('Bash', {'command': cmd}, task_id=TASK, **kw)


def denied(cmd, **kw):
    return bash(cmd, **kw) is not None


def read_denied(path, **kw):
    return (
        vendor_guard_deny('Read', {'file_path': path}, task_id=TASK, **kw)
        is not None
    )


# ── the runaways' command shapes, denied ────────────────────────────


class TestRunawayShapes:
    @pytest.mark.parametrize(
        'cmd',
        [
            # runaway 1: the vendor's own log / config / key hunt
            'grep -rl "apiKey" ~/AppData/Roaming/GuardTest Global',
            'cat ~/AppData/Roaming/GuardTest Global/config.json',
            'ls -d /mnt/c/Users/*/AppData/Roaming/GuardTest*',
            'find /mnt/c/Users -maxdepth 5 -iname "*guardtest*" -type d',
            'find / -maxdepth 7 -name "logins.json" 2>/dev/null',
            # the profile DB copies (runaway 2)
            'cp "/mnt/c/.GuardTestData/cache/x_flower/cookies.sqlite" /tmp/',
            'cp "/mnt/c/.GuardTestData/cache/x/Default/Login Data" /tmp/x',
            'cp "/mnt/c/.GuardTestData/cache/x/places.sqlite" /tmp/',
            'sqlite3 /tmp/p13/formhistory.sqlite "select * from moz_formhistory"',
            # the vendor's local API port
            'curl -s --max-time 3 http://127.0.0.1:59999/status',
            'curl -s http://localhost:59999/api/v1/user/list',
            # incident 97db66d2: lateral movement
            'ssh mac ls',
            'ssh -f -N -L 5545:127.0.0.1:54345 mac',
            'scp file.txt mac:/tmp/',
            'sftp mac',
            'rsync -e ssh ./ mac:/tmp/',
            'cat ~/.ssh/config',
            'cat ~/.ssh/known_hosts',
            'tr "\\0" "\\n" < /proc/865399/environ',
            'cat /proc/865399/cmdline',
            'ps aux | grep -i guardtest',
            'ps -ef',
            'lsof -i :59999',
            # the CDP bridge on a proxy port
            'python3 -m http.server 9222',
            'socat tcp-listen:9330,fork tcp:127.0.0.1:59999',
            # OS-settings mutation (incident ccef25ee, dummy target)
            'reg add "HKCU:\\Software\\VibeSellerGuardTest\\Internet'
            ' Settings" /v ProxyServer /d 192.0.2.10:7890',
            'powershell.exe -Command "Set-ItemProperty -Path'
            ' HKCU:\\Software\\VibeSellerGuardTest\\Internet Settings'
            ' -Name ProxyServer -Value 192.0.2.10:7890"',
            'netsh winhttp set proxy 192.0.2.10:7890',
            'setx HTTP_PROXY http://192.0.2.10',
            'schtasks /Create /TN x /TR y',
            'sc config wuauserv start= disabled',
            'echo "192.0.2.10 example.com" >> /etc/hosts',
            'networksetup -setwebproxy Wi-Fi 192.0.2.10',
            'defaults write com.vibeseller.guardtest Probe -string x',
            'launchctl load ~/Library/LaunchAgents/x.plist',
            'systemctl --user enable myunit',
            'echo "export HTTP_PROXY=1" >> ~/.bashrc',
            'sed -i s/a/b/ ~/.profile',
            # incident 2 (native Windows, task d7ba7202): profile DBs
            # copied into %TEMP% and queried with sqlite
            'Copy-Item "C:\\Users\\u\\AppData\\Roaming\\GuardTest'
            ' Global\\p1\\Default\\History" $env:TEMP\\h.db',
            'xcopy "C:\\Users\\u\\AppData\\Roaming\\GuardTest'
            ' Global\\p1\\Default\\Login Data" %TEMP%\\l.db',
            'robocopy "C:\\Users\\u\\AppData\\Roaming\\GuardTest'
            ' Global\\p1\\Default" %TEMP%\\p',
            'python3 -c "import shutil;'
            " shutil.copy('/x/p1/Default/History', '/tmp/h.db')\"",
            'sqlite3 "/x/p1/Default/Cookies" .tables',
            # agent-built port forwarders (the 7777->7797 incident)
            'socat tcp-listen:17797,fork tcp:127.0.0.1:7777',
            'nc -l 17797',
            'netsh interface portproxy add v4tov4 17797 127.0.0.1 7777',
            'powershell.exe -Command "New-NetFirewallRule -DisplayName'
            ' x -LocalPort 17797"',
            "python3 -c \"import socket; s=socket.socket();"
            " s.bind(('127.0.0.1', 17797));"
            " s.connect(('127.0.0.1', 7777))\"",
            # the vibe-seller home beyond the workspace
            f'cd {VH} && sqlite3 data/vibe_seller.db .tables',
            f'cat {VH}/profiles.json',
            f'ls {VH}/r/',
        ],
    )
    def test_denied(self, fake_vendor, cmd):
        assert denied(cmd), cmd

    def test_write_content_binding_a_cdp_port(self, fake_vendor):
        # The incident's cdp_bridge.py carried the port only in the
        # written file's content — the Write call is denied too.
        assert (
            vendor_guard_deny(
                'Write',
                {
                    'file_path': '/tmp/bridge.py',
                    'content': 'LISTEN_PORT = 9222',
                },
                task_id=TASK,
            )
            is not None
        )


# ── the corpus: ordinary task commands, allowed ────────────────────


class TestCorpus:
    @pytest.mark.parametrize(
        'cmd',
        [
            'ls -la && cat stores/1/STORE.md',
            'grep -ril "guardtest" .',
            'cat knowledge/project/common/amazon-sites.md',
            'python3 -c "import pandas as pd;'
            " df = pd.read_csv('stores/1/cookies-report.csv');"
            ' print(df.Cookies)"',
            "browser-use <<'PY'\nclick('Accept Cookies')\nPY",
            'browser-use <<\'PY\'\njs("return document.cookie")\nPY',
            "browser-use <<'PY'\nfind('#accept-cookies')\nPY",
            'sqlite3 /tmp/myown.sqlite .tables',
            'curl -s https://sellercentral.amazon.com/home',
            'curl -s --max-time 3 http://127.0.0.1:9999/status',
            'sleep 30 && echo done',
            'git log --oneline -5',
            'grep alias ~/.bashrc',
            'cat /etc/hostname',
            'defaults read com.apple.finder',
            'python3 -c "print(sum(range(9222)))"',
            'socat -u file:a file:b',
            'nc -z 127.0.0.1 7777',
            # the renamed-copy evasion is a stated residual: the copy
            # itself is denied at the SOURCE, so there is no copy
            'sqlite3 /tmp/h.db "select * from urls"',
        ],
    )
    def test_allowed(self, fake_vendor, cmd):
        assert not denied(cmd), cmd

    def test_own_proxy_port_is_allowed(self, fake_vendor):
        assert not denied(
            'curl -sf http://127.0.0.1:9223/json/version',
            own_ports=frozenset({9223}),
        )

    def test_runtime_vendor_port_from_a_configured_account(self):
        # D-C3-5: a customised ziniao account port is denied even
        # though no spec declares it.
        assert denied(
            'curl http://127.0.0.1:17100/status',
            extra_vendor_ports=frozenset({17100}),
        )


class TestBashSafetyForms:
    """The scoped process forms bash_safety RECOMMENDS must keep
    working — extracted from its own deny message, so the two guards
    cannot drift apart (D-C3-7)."""

    def test_every_recommended_form_passes(self):
        bullets = [
            code
            for line in bash_safety._DENY_REASON_TEMPLATE.splitlines()
            if line.strip().startswith('•')
            for code in re.findall(r'`([^`]+)`', line)
        ]
        assert bullets, 'bash_safety message changed shape'
        for form in bullets:
            assert not denied(form), form

    @pytest.mark.parametrize(
        'cmd',
        [
            'ps -o args -p $$',
            'ps -o pid,command -p 1234',
            'pgrep -P $$ browser-use',
            'pkill -P $$ sleep',
            'pkill -P 1234 python3',
            'kill $PID',
            'lsof -p 1234',
            'tasklist.exe /FI "PID eq 1234"',
        ],
    )
    def test_scoped_forms_pass(self, cmd):
        assert not denied(cmd), cmd


# ── the task workspace and its own files ────────────────────────────


class TestTaskWorkspace:
    def test_workspace_masked_from_dir_names(self, fake_vendor, tmp_path):
        # A store slugged like the vendor's dir: inside the task's own
        # workspace it reads normally.
        task_dir = tmp_path / 'tasks' / TASK
        assert not denied(
            f'cat {task_dir}/stores/guardtestdata/notes.md',
            task_dir=str(task_dir),
        )

    def test_own_bh_tmp_sessions(self, fake_vendor):
        # D-C3-9: every session kind the wrappers really assign —
        # store default, worker slot, web, and the prefix-less aux
        # (owned by exact name).
        for name in (
            f'store1-{PREFIX}',
            f'store1-{PREFIX}-w2',
            f'web-{PREFIX}',
        ):
            assert not read_denied(f'{VH}/bh-tmp/{name}/shot.png')
        assert not read_denied(
            f'{VH}/bh-tmp/store1-aux/shot.png',
            owned_session_names=frozenset({'store1-aux'}),
        )

    def test_other_sessions_bh_tmp_denied(self, fake_vendor):
        assert read_denied(f'{VH}/bh-tmp/other-session/shot.png')
        assert read_denied(f'{VH}/bh-tmp/store2-aux/shot.png')

    def test_under_closed_home_kinds(self):
        assert under_closed_home(VH, PREFIX)
        assert under_closed_home(f'{VH}/profiles.json', PREFIX)
        assert not under_closed_home(f'{VH}/stores/1', PREFIX)
        assert not under_closed_home(f'{VH}/bh-tmp/x-{PREFIX}', PREFIX)


# ── the output tripwire (PATH ONLY — user decision) ─────────────────


class TestTripwire:
    @pytest.mark.parametrize(
        'text',
        [
            '/mnt/c/.GuardTestData/log line',
            f'copied {VH}/../.GuardTestData/log',
            'read /mnt/c/Users/u/AppData/Roaming/GuardTest Global/config',
            'copied to /tmp/p13/Login Data (44 bytes)',
        ],
    )
    def test_paths_trip(self, fake_vendor, text):
        assert scan_tool_result(text) is not None, text

    @pytest.mark.parametrize(
        'text',
        [
            # key/token shapes are NOT matched (PATH ONLY)
            '{"loginToken":"835c2c116969925958c5c754acfe405000a44297311a9d42"}',
            'SQLite format 3\0\x10raw bytes',
            'moz_cookies rows: 12',
            # ordinary page text and deliverables
            'Accept Cookies and our privacy policy',
            'saved to stores/1/cookies-report.csv',
            'the page mentions cookies policy',
            # checksums and SHAs of other lengths
            'sha256: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
            'commit abc1234567890abc1234567890abc1234567890',
        ],
    )
    def test_non_paths_do_not_trip(self, fake_vendor, text):
        assert scan_tool_result(text) is None, text
