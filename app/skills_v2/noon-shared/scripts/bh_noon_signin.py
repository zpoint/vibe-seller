# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, wait_for_load, ...)
"""noon's email-OTP sign-in, in two deterministic calls.

§1 of SKILL.md, made mechanical. Driven by hand it cost 20-25 calls per
sign-in in live runs: "Continue with this user" is a `<p>` inside a
clickable div, so every `button` query missed it; the OTP field renders
seconds late and re-renders, so `fill_input` raced it; and each "Try
again" minted a new code, so the code just read was already stale.

Call 1 (no OTP) -- request the code:

  browser-use < .claude/skills/noon-shared/scripts/bh_noon_signin.py
  -> RESULT {"ok": true, "stage": "otp_sent", "sent_at": <epoch>, ...}
     or     {"ok": true, "stage": "signed_in"} when the session is live

Then fetch the newest noon OTP email that arrived AFTER `sent_at`
(SKILL.md §1 Case A: `vibe_seller_sync_email_now`, then the db_path from
`vibe_seller_email_info`), and

Call 2 -- submit it, without clicking "Try again" in between:

  OTP=123456 browser-use < .claude/skills/noon-shared/scripts/bh_noon_signin.py
  -> RESULT {"ok": true, "stage": "signed_in"}  or a reason

It never types an email address or a password: the account the page
offers is the one it signs in to.
"""

import json
import os
import time

LOGIN = 'https://login.noon.partners/en/'
OTP = os.environ.get('OTP', '').strip()
out = {'ok': False, 'stage': None}


def _finish(reason=None):
    if reason:
        out['reason'] = reason
    out['url'] = js('return location.href')
    print('RESULT ' + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)


def _text():
    return (js('return document.body.innerText') or '').replace('\n', ' ')


def _signed_in():
    href = js('return location.href') or ''
    return 'login.noon.partners' not in href


def _otp_box():
    """The OTP field once it is on screen: a short text input."""
    return js(
        "var i=Array.from(document.querySelectorAll('input')).find("
        'function(x){var r=x.getBoundingClientRect();return r.width>0&&'
        "(x.maxLength===6||/otp|code/i.test(x.name+' '+x.placeholder+' '+"
        "x.autocomplete));});return i?'yes':null;"
    )


def _click_text(pattern):
    """Click the SMALLEST visible element whose text matches -- the label
    is a <p> inside a clickable div, and it wraps across lines."""
    box = js(
        f'var re=/{pattern}/i,best=null;'
        "document.querySelectorAll('button,a,div,p,span').forEach(function(e){"
        "var t=(e.innerText||'').replace(/\\s+/g,' ').trim();"
        'var r=e.getBoundingClientRect();'
        'if(!re.test(t)||r.width===0||r.height===0)return;'
        'if(!best||r.width*r.height<best.a)best={a:r.width*r.height,'
        'x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};});'
        'return best?JSON.stringify(best):null;'
    )
    if not box:
        return False
    p = json.loads(box)
    click_at_xy(p['x'], p['y'])
    return True


def _wait(pred, budget):
    end = time.time() + budget
    while time.time() < end:
        if pred():
            return True
        time.sleep(1.5)
    return False


if not OTP:
    new_tab(LOGIN)
    wait_for_load()
    _wait(lambda: len(_text()) > 40 or _signed_in(), 25)
    if _signed_in():
        out.update(ok=True, stage='signed_in')
        _finish()
    if not _otp_box():
        if not _click_text('continue with this user|log in with otp|send otp'):
            capture_screenshot()
            _finish(
                'no "Continue with this user" control -- read the '
                'screenshot; the account picker may need the user'
            )
    out['sent_at'] = int(time.time()) - 5
    if not _wait(_otp_box, 30):
        capture_screenshot()
        _finish('the OTP field never appeared -- read the screenshot')
    m = None
    t = _text()
    if 'try again in' in t.lower():
        m = t.lower().split('try again in', 1)[1][:20]
        out['resend_throttled'] = m.strip()
    out.update(ok=True, stage='otp_sent')
    _finish(
        'code requested: read the newest noon OTP email received after '
        'sent_at, then re-run with OTP=<code>. Do not click "Try again" -- '
        'it makes the code you are about to read stale.'
    )

# Call 2: the page is still on the OTP step from call 1.
if _signed_in():
    out.update(ok=True, stage='signed_in')
    _finish()
if not _wait(_otp_box, 20):
    capture_screenshot()
    _finish('no OTP field on the page -- run without OTP first')
# A focused insert reaches the field; a JS value set races its re-render.
obj = cdp(
    'Runtime.evaluate',
    expression=(
        "Array.from(document.querySelectorAll('input')).find(function(x){"
        'var r=x.getBoundingClientRect();return r.width>0&&'
        "(x.maxLength===6||/otp|code/i.test(x.name+' '+x.placeholder+' '+"
        'x.autocomplete));})'
    ),
    returnByValue=False,
)
oid = (obj.get('result') or {}).get('objectId')
if not oid:
    _finish('the OTP field vanished before it could be filled -- re-run')
cdp('DOM.focus', objectId=oid)
js(
    'var i=document.activeElement;if(i&&i.select){i.select();}'
    "document.execCommand('delete');"
)
cdp('Input.insertText', text=OTP)
time.sleep(1)
if not _click_text('^continue$|^verify$|^submit$'):
    for kind in ('keyDown', 'keyUp'):
        cdp(
            'Input.dispatchKeyEvent',
            type=kind,
            key='Enter',
            code='Enter',
            windowsVirtualKeyCode=13,
        )
_wait(lambda: _signed_in() or 'invalid otp' in _text().lower(), 25)
if 'invalid otp' in _text().lower():
    _finish(
        'Invalid OTP: the code is stale or mistyped, not blocked. Read the '
        'NEWEST noon email received after sent_at (copy db_path from '
        'vibe_seller_email_info) and re-run with it.'
    )
if not _signed_in():
    capture_screenshot()
    _finish('still on the login page after submitting -- read the screenshot')
js(
    "var b=Array.from(document.querySelectorAll('button')).find("
    'function(x){return /maybe later/i.test(x.textContent);});'
    'if(b){b.click();}'
)
out.update(ok=True, stage='signed_in')
_finish()
