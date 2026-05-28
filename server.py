import re
from collections import defaultdict
from pathlib import Path
from flask import Flask, render_template_string

app = Flask(__name__)
LOG_FILE = Path(__file__).parent / "prices.log"

ENTRY_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+\[(.+?)\]\s+\$(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)(\s+\*\*\*.*)?$")

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <title>Airfare Monitor</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: #f5f5f5;
      color: #222;
      padding: 40px 20px;
    }
    .container { max-width: 860px; margin: 0 auto; }
    header { margin-bottom: 36px; }
    header h1 { font-size: 1.6rem; font-weight: 700; }
    header p { color: #777; font-size: 0.875rem; margin-top: 4px; }
    .card {
      background: white;
      border-radius: 10px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      margin-bottom: 28px;
      overflow: hidden;
    }
    .card-header {
      padding: 14px 20px;
      background: #fafafa;
      border-bottom: 1px solid #eee;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-header h2 { font-size: 1rem; font-weight: 600; }
    .badge {
      font-size: 0.75rem;
      padding: 3px 10px;
      border-radius: 99px;
      background: #e5e7eb;
      color: #555;
    }
    .badge.alert { background: #dcfce7; color: #16a34a; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; }
    th {
      text-align: left;
      padding: 10px 20px;
      font-size: 0.8rem;
      color: #888;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      border-bottom: 1px solid #eee;
    }
    td { padding: 11px 20px; border-bottom: 1px solid #f0f0f0; font-size: 0.95rem; }
    tr:last-child td { border-bottom: none; }
    tr.latest td { background: #fffdf0; }
    .price { font-weight: 700; font-size: 1.05rem; }
    .below { color: #16a34a; font-weight: 600; }
    .empty { padding: 32px 20px; color: #aaa; font-style: italic; text-align: center; }
    .no-log { text-align: center; padding: 60px 20px; color: #aaa; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Airfare Monitor</h1>
      <p>Auto-refreshes every 5 minutes &mdash; {{ log_path }}</p>
    </header>

    {% if not history %}
      <div class="no-log">No price data yet. Run <code>python monitor.py</code> to start tracking.</div>
    {% else %}
      {% for name, entries in history.items() %}
        {% set latest = entries[-1] %}
        <div class="card">
          <div class="card-header">
            <h2>{{ name }}</h2>
            <span class="badge {{ 'alert' if latest.alert else '' }}">
              {% if latest.alert %}
                ✓ Below threshold &mdash; ${{ latest.price }}
              {% else %}
                Latest: ${{ latest.price }}
              {% endif %}
            </span>
          </div>
          {% if entries %}
          <table>
            <thead>
              <tr><th>Checked</th><th>Price</th><th>Airline</th><th>Departs</th><th>Stops</th><th>Status</th></tr>
            </thead>
            <tbody>
              {% for entry in entries | reverse %}
              <tr {% if loop.first %}class="latest"{% endif %}>
                <td>{{ entry.timestamp }}</td>
                <td class="price">${{ entry.price }}</td>
                <td>{{ entry.get("airline", "—") }}</td>
                <td>{{ entry.get("dep_time", "—") }}</td>
                <td>{{ entry.get("stops", "—") }}</td>
                <td {% if entry.alert %}class="below"{% endif %}>
                  {{ "✓ Below threshold" if entry.alert else "—" }}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          {% else %}
          <div class="empty">No entries yet.</div>
          {% endif %}
        </div>
      {% endfor %}
    {% endif %}
  </div>
</body>
</html>
"""


def parse_log() -> dict:
    history = defaultdict(list)
    if not LOG_FILE.exists():
        return history
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = ENTRY_RE.match(line.strip())
            if m:
                ts, name, price, airline, dep_time, stops, alert_flag = m.groups()
                history[name].append({
                    "timestamp": ts,
                    "price": int(price),
                    "airline": airline.strip(),
                    "dep_time": dep_time.strip(),
                    "stops": stops.strip(),
                    "alert": bool(alert_flag),
                })
    return history


@app.route("/")
def index():
    history = parse_log()
    return render_template_string(TEMPLATE, history=history, log_path=str(LOG_FILE))


if __name__ == "__main__":
    print(f"Dashboard at http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
