"""Full-page screenshot of a live Streamlit app via Chrome DevTools Protocol.

--virtual-time-budget fast-forwards timers, but Streamlit's content arrives over
a websocket, so the render never completes and you photograph the skeleton
loader. This waits in real time, polls the DOM until the app has actually
painted, then captures the whole scroll height.

usage: py -3 shoot.py <url> <out.png> [width] [wait_seconds]
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9223
PROFILE = "C:/Users/Amadeus/AppData/Local/Temp/shootprofile"

URL = sys.argv[1]
OUT = sys.argv[2]
WIDTH = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
WAIT = float(sys.argv[4]) if len(sys.argv) > 4 else 25.0

READY_JS = """
(() => {
  const skeleton = document.querySelectorAll('[class*="skeleton"],[data-testid*="Skeleton"]').length;
  const nums = document.querySelectorAll('.num').length;
  const plots = document.querySelectorAll('.js-plotly-plot .main-svg').length;
  return JSON.stringify({skeleton: skeleton, nums: nums, plots: plots});
})()
"""

# Streamlit scrolls inside its own container, so document.body reports 0.
HEIGHT_JS = """
(() => {
  const sel = 'section.main,[data-testid="stAppViewContainer"],' +
              '[data-testid="stMain"],[data-testid="stMainBlockContainer"]';
  const nodes = [document.body, document.documentElement].concat(
    Array.from(document.querySelectorAll(sel)));
  let best = 0;
  for (const n of nodes) {
    if (!n) continue;
    best = Math.max(best, n.scrollHeight || 0, n.getBoundingClientRect().height || 0);
  }
  return best;
})()
"""

proc = subprocess.Popen(
    [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
     f"--remote-debugging-port={PORT}", f"--window-size={WIDTH},1200",
     "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*",
     "--user-data-dir=" + PROFILE, "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

try:
    targets = None
    for _ in range(60):
        try:
            targets = json.loads(
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2).read())
            if targets:
                break
        except Exception:
            time.sleep(0.5)
    if not targets:
        raise SystemExit("chrome devtools never came up")

    page = next(t for t in targets if t["type"] == "page")
    ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                     timeout=120, max_size=300 * 1024 * 1024)
    counter = [0]

    def send(method, **params):
        counter[0] += 1
        ws.send(json.dumps({"id": counter[0], "method": method, "params": params}))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == counter[0]:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    def evaluate(expression):
        result = send("Runtime.evaluate", returnByValue=True, expression=expression)
        return result.get("result", {}).get("value")

    send("Page.enable")
    send("Runtime.enable")
    send("Page.navigate", url=URL)

    state, settled, deadline = {}, 0.0, time.time() + WAIT
    while time.time() < deadline:
        time.sleep(1.0)
        raw = evaluate(READY_JS)
        if not raw:
            continue
        state = json.loads(raw)
        if state["skeleton"] == 0 and state["nums"] >= 4 and state["plots"] >= 2:
            settled += 1.0
            if settled >= 3.0:      # let fonts and chart animation land
                break

    height = int(evaluate(HEIGHT_JS) or 0)
    height = min(max(height + 100, 1200), 16000)
    send("Emulation.setDeviceMetricsOverride", width=WIDTH, height=height,
         deviceScaleFactor=1, mobile=False)
    time.sleep(2.5)
    shot = send("Page.captureScreenshot", format="png", captureBeyondViewport=True)
    with open(OUT, "wb") as handle:
        handle.write(base64.b64decode(shot["data"]))
    print(f"captured {WIDTH}x{height} -> {OUT}  state={state}")
finally:
    proc.terminate()
