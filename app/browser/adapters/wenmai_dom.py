"""Pure DOM logic for the Wenmai (稳卖) renderer + middle page.

Two surfaces, both driven without reading the vendor's JS into this
repo:

- The Electron client's login page (``https://bs.hitoor.com/#/login``),
  driven through the Node inspector. Enterprise login only — the phone
  tab has an image captcha. Fields are found by placeholder and set via
  the native value setter + ``input`` event (Vue v-model ignores a
  plain ``el.value = x``). Verified live 2026-11-27, client v5.1.0715.
- The store browser's entry middle page
  (``chrome-extension://<id>/middlePage/index.html``), driven over CDP
  after ``openEnv``: a 店铺信息 card whose ``button.btn-primary`` is
  打开店铺. Matched by the ``middlePage`` path — never by the extension
  id, which changes per install.
"""

import json

# Route hash of the client's main window. The login page is '#/login';
# a successful login leaves it (observed: '#/guide').
LOGIN_HASH_JS = 'location.hash'


def is_logged_in_hash(route_hash: str | None) -> bool:
    """True when the main window is NOT on the login page.

    The logged-in app sits on any non-login hash; a bare/empty hash is
    a window still loading, which cannot be logged in yet.
    """
    h = (route_hash or '').strip().lower()
    if not h or h in ('#', '#/'):
        return False
    return not h.startswith('#/login')


def fill_enterprise_login_js(company: str, username: str, password: str) -> str:
    """JS driving the enterprise (企业登录) form on the login page.

    Clicks the enterprise tab, fills 企业名称 / 企业用户名 / 企业密码 by
    placeholder, ticks the agreement checkbox when unticked, and clicks
    the visible 登录 button. Returns 'OK', or 'NOTAB' / 'NOFIELDS' /
    'NOBTN' naming what was missing so a client UI change is pinpointed.
    The password only lives in this one eval — never logged.
    """
    return (
        '(async function(company,username,password){'
        'function setv(el,v){var s=Object.getOwnPropertyDescriptor('
        "window.HTMLInputElement.prototype,'value').set;s.call(el,v);"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));}"
        "var tab=document.querySelector('#tab-enterprise');"
        "if(!tab)return 'NOTAB';tab.click();"
        'await new Promise(function(r){setTimeout(r,800);});'
        'function f(ph){return Array.from('
        "document.querySelectorAll('input')).find(function(i){"
        "return (i.getAttribute('placeholder')||'').indexOf(ph)>=0;});}"
        "var c=f('企业名称'),u=f('企业用户名'),p=f('企业密码');"
        "if(!c||!u||!p)return 'NOFIELDS';"
        'setv(c,company);setv(u,username);setv(p,password);'
        # The agreement box (我已阅读并同意 服务协议 & 隐私政策) is
        # checked by default; tick it only when it is not.
        "var ag=Array.from(document.querySelectorAll('.el-checkbox'))"
        '.find(function(e){return e.offsetParent&&/阅读并同意|服务协议/'
        ".test(e.innerText||'');});"
        "if(ag){var ri=ag.querySelector('input');"
        'if(ri&&!ri.checked)ag.click();}'
        "var btn=Array.from(document.querySelectorAll('button'))"
        '.find(function(b){return b.offsetParent&&'
        "(b.innerText||'').trim()==='登录';});"
        "if(!btn)return 'NOBTN';btn.click();return 'OK';"
        '})('
        + json.dumps(company)
        + ','
        + json.dumps(username)
        + ','
        + json.dumps(password)
        + ')'
    )


# Click 打开店铺 on the store browser's middle page (a 店铺信息 card
# whose primary button is 打开店铺). 'OK' / 'NOBTN'.
CLICK_OPEN_STORE_JS = (
    '(function(){'
    "var b=document.querySelector('button.btn-primary');"
    "if(!b)return 'NOBTN';b.click();return 'OK';})()"
)
