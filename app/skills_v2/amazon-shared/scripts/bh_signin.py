# ruff: noqa: F821 — browser-harness globals (new_tab, js, wait_for_load, ...)
"""Walk Seller Central's sign-in challenge loop, as far as the DOM allows.

The §2 loop in SKILL.md, made deterministic: an agent that hand-drives it
spends six to ten calls per sign-in -- `js()` snippets that return nothing
because they lack a top-level `return`, a Continue click that silently
no-ops, a submit that needs `form.requestSubmit()` -- and bulk pages demand
a fresh sign-in every few minutes, so it is paid again after every wait for
a batch. Run through the STORE WRAPPER:

  SC_HOST=sellercentral.amazon.sa browser-use < .claude/skills/amazon-shared/scripts/bh_signin.py

Prints exactly one ``RESULT {json}`` line:
  ok          -- true once the page is off /ap/ (signed in)
  steps       -- what was done, in order
  url         -- where it stopped
  needs       -- when not ok: 'passkey' (Ziniao's hosted-passkey overlay is
                 native and must be coordinate-clicked, §2c), 'human' (a
                 challenge Ziniao did not fill), 'broken_env'
                 (navigator.webdriver is true -- stop, §2) or 'stuck'
  reason      -- what to do next

It never types a credential: every value it submits is one Ziniao filled.
"""

import json
import os
import time


def _env(name):
    """A required parameter, or one RESULT line saying how to pass it.

    A plain `NAME=value` line before the command never reaches this
    script; an agent did exactly that and got a bare KeyError traceback.
    """
    value = os.environ.get(name, '').strip()
    if not value:
        print(
            'RESULT '
            + json.dumps({
                'ok': False,
                'reason': f'{name} is not set -- put it on the same line '
                'as browser-use (NAME=value browser-use < script) or '
                'export it',
            })
        )
        raise SystemExit(0)
    return value


HOST = _env('SC_HOST')
# The wrapper kills a call at 120s; leave room for the RESULT line.
_DEADLINE = time.time() + int(os.environ.get('SIGNIN_BUDGET', '95'))
out = {'ok': False, 'steps': [], 'url': None, 'host': HOST}


def _finish(needs=None, reason=None):
    out['url'] = js('return location.href')
    if needs:
        out['needs'] = needs
    if reason:
        out['reason'] = reason
    print('RESULT ' + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)


_STATE_JS = (
    'var q=function(s){return document.querySelector(s);};'
    'var otp=q("input[name=otpCode],input#auth-mfa-otpcode");'
    'var pw=q("#ap_password");'
    'return JSON.stringify({url:location.href,'
    'webdriver:navigator.webdriver===true,'
    'email:!!q("#ap_email"),cont:!!q("#continue"),'
    'pw:!!pw,pw_filled:!!(pw&&pw.value),'
    'otp:!!otp,otp_filled:!!(otp&&otp.value),'
    'submit:!!q("#signInSubmit,#auth-signin-button"),'
    'passkey:!!q("a[href*=passkey],#passkey-sign-in,[data-passkey]")});'
)


def _state():
    try:
        return json.loads(js(_STATE_JS) or '{}')
    except (TypeError, ValueError):
        return {}


def _press(selector):
    """Click, and if the page does not move, submit the button's form.

    A JS click on Amazon's submit sometimes no-ops; requestSubmit() on the
    enclosing form is what a human's Enter key does.
    """
    before = js('return location.href')
    js(
        f'var b=document.querySelector("{selector}");'
        'if(b){b.click();return true;}return false;'
    )
    time.sleep(4)
    if js('return location.href') == before:
        js(
            f'var b=document.querySelector("{selector}");'
            'var f=b&&b.closest("form");if(f){f.requestSubmit();}'
        )
        time.sleep(4)


new_tab(f'https://{HOST}/home')
wait_for_load()
last, same = None, 0
while time.time() < _DEADLINE:
    st = _state()
    url = st.get('url') or ''
    if st.get('webdriver'):
        _finish(
            'broken_env',
            'navigator.webdriver is true: the Ziniao layer did not load. '
            'Stop and report it; do not ask the user for a password.',
        )
    if url and '/ap/' not in url:
        out['ok'] = True
        _finish()
    key = json.dumps(st, sort_keys=True)
    same = same + 1 if key == last else 0
    last = key
    if same >= 3:
        capture_screenshot()
        _finish(
            'stuck',
            'the sign-in page did not change across three attempts; read '
            'the screenshot and resolve the challenge shown (SKILL.md §2).',
        )
    if '/ap/mfa' in url or st.get('otp'):
        if st.get('otp_filled'):
            out['steps'].append('otp')
            # The OTP page's submit is #auth-signin-button (seen live); the
            # password page's is #signInSubmit. querySelector takes the first.
            _press('#auth-signin-button,#signInSubmit')
        else:  # Ziniao's OTP service fetches the code; give it time
            time.sleep(3)
        continue
    if st.get('pw'):
        if st.get('pw_filled'):
            out['steps'].append('password')
            _press('#signInSubmit')
            continue
        if st.get('passkey'):
            _finish(
                'passkey',
                'password is not filled and a passkey sign-in is offered: '
                'follow SKILL.md §2c (click "Sign in with a passkey", then '
                'coordinate-click the 已托管账号Passkey overlay).',
            )
        time.sleep(3)  # Ziniao may still be filling it
        continue
    if st.get('cont'):
        out['steps'].append('continue')
        _press('#continue')
        continue
    time.sleep(3)
capture_screenshot()
_finish(
    'human',
    'sign-in did not finish within the time budget; read the screenshot '
    'and resolve the remaining challenge (SKILL.md §2), asking the user if '
    'Ziniao offers nothing for it.',
)
