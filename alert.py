#!/usr/bin/env python3
"""
One-shot check for GitHub Actions: OPENAI price on Lighter Core vs Lighter Robinhood.
Sends a Telegram message when |core - robinhood| <= SPREAD_USD, then stays quiet
until the spread goes above REARM_USD. Needs only the Python standard library.

Secrets (GitHub -> Settings -> Secrets and variables -> Actions):
  TG_BOT_TOKEN, TG_CHAT_ID
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CORE_URL = "https://mainnet.zklighter.elliot.ai"
RH_URL = "https://api.rh.lighter.xyz"

SYMBOL = os.environ.get("SYMBOL", "OPENAI").upper()
PRICE_FIELD = os.environ.get("PRICE_FIELD", "mark_price")
SPREAD_USD = float(os.environ.get("SPREAD_USD", "1.0"))
REARM_USD = float(os.environ.get("REARM_USD", str(SPREAD_USD + 0.5)))
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
STATE_FILE = "state.json"


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "lighter-spread-alert/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_market(details, symbol):
    perps = details.get("order_book_details") or []
    names = [str(m.get("symbol", "")) for m in perps]
    exact = [m for m in perps if str(m.get("symbol", "")).upper() == symbol]
    if exact:
        return exact[0], names
    part = [m for m in perps if symbol in str(m.get("symbol", "")).upper()]
    return (part[0] if part else None), names


def get_price(base_url, symbol, field):
    data = http_get_json(f"{base_url}/api/v1/orderBookDetails?filter=perp")
    market, names = pick_market(data, symbol)
    if market is None:
        similar = [n for n in names if symbol[:3] in n.upper()][:10]
        raise LookupError(f"market '{symbol}' not found on {base_url}. Similar: {similar or 'none'}")
    raw = market.get(field)
    if raw in (None, "", 0, "0"):
        raise LookupError(f"{market.get('symbol')} on {base_url} has no usable '{field}' (got {raw!r})")
    return str(market.get("symbol")), float(raw)


def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        raise RuntimeError("TG_BOT_TOKEN / TG_CHAT_ID are not set")
    body = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": text}).encode()
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram error: {payload}")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
            return {"armed": bool(s.get("armed", True)), "missing_notified": bool(s.get("missing_notified", False))}
    except (OSError, ValueError):
        return {"armed": True, "missing_notified": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def main():
    if os.environ.get("TEST"):
        send_telegram("Lighter spread alert: test message OK")
        print("test message sent")
        return

    state = load_state()
    try:
        sym_core, p_core = get_price(CORE_URL, SYMBOL, PRICE_FIELD)
        sym_rh, p_rh = get_price(RH_URL, SYMBOL, PRICE_FIELD)
    except LookupError as e:
        print(f"warning: {e}")
        if not state["missing_notified"]:
            try:
                send_telegram(f"Lighter spread alert: {e}\nSet the SYMBOL / PRICE_FIELD variables in the workflow.")
                state["missing_notified"] = True
            except (urllib.error.URLError, RuntimeError, ValueError) as te:
                print(f"warning: could not notify: {te}")
        save_state(state)
        return
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"warning (network, will retry next run): {e}")
        save_state(state)
        return

    state["missing_notified"] = False
    spread = abs(p_core - p_rh)
    print(f"Core {sym_core}={p_core:.4f}  RH {sym_rh}={p_rh:.4f}  spread=${spread:.4f}  armed={state['armed']}")

    if state["armed"] and spread <= SPREAD_USD:
        try:
            send_telegram(
                f"{SYMBOL} prices converged on Lighter\n"
                f"Core: ${p_core:.4f}\nRobinhood: ${p_rh:.4f}\n"
                f"Spread: ${spread:.4f} (threshold ${SPREAD_USD})"
            )
            state["armed"] = False
            print("ALERT sent")
        except (urllib.error.URLError, RuntimeError, ValueError) as e:
            print(f"warning: alert not sent, will retry: {e}")
    elif not state["armed"] and spread > REARM_USD:
        state["armed"] = True
        print("re-armed")

    save_state(state)


if __name__ == "__main__":
    sys.exit(main())
