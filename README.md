# BruteBox

A web-based 4-digit MPIN brute force testing tool built for authorized penetration testing and security research. BruteBox automates credential testing against login endpoints that use CSRF-protected POST forms, with real-time progress reporting via WebSocket.

**Live Demo:** https://brutebox.onrender.com/

---

## Features

- Threaded attack engine — 8 concurrent workers with session pooling
- CSRF token harvesting and automatic session management
- Three-phase attack: custom wordlist -> common PIN dictionary -> full 0000-9999 keyspace
- Pause and resume support — attack can be suspended without losing progress
- Real-time console output via Socket.IO with typewriter effect
- Website security rating based on observed defensive signals (rate limiting, IP blocking, CSRF rotation, response delay)
- MPIN strength analysis with common PIN classification
- Session report — copyable and exportable as `.txt`
- Matrix rain background, pulsing stats bar, LED status indicator

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask, Flask-SocketIO |
| Concurrency | `threading`, `concurrent.futures.ThreadPoolExecutor` |
| HTTP | `requests`, `BeautifulSoup4` |
| Frontend | HTML, CSS, Vanilla JS |
| Realtime | Socket.IO |
| Deployment | Gunicorn + Eventlet, Render |

---

## How It Works

1. A session pool of 10 HTTP sessions is created, each fetching a fresh CSRF token from the target login page
2. Custom PINs (if provided) are tested first
3. 40 common PINs are tested next using the dictionary phase
4. Remaining PINs (0000-9999) are tested in chunks of 100 using 8 threads
5. Any timed-out PINs are collected and retried at the end
6. Throughout the attack, defensive signals are tracked and used to generate a security rating on completion

---

## Installation
```bash
git clone https://github.com/codebysreyas/BruteBox.git
cd BruteBox
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` in your browser.

---

## Requirements
```
Flask
Flask-SocketIO
requests
beautifulsoup4
python-socketio
eventlet
gunicorn
```

---

## Usage

1. Enter the target login URL (must be the actual POST endpoint)
2. Enter the username to test against
3. Optionally expand the custom PIN wordlist and paste your own PINs
4. Accept the authorization disclaimer
5. Click `[ START ]`

The console displays real-time progress. On completion, a security rating and MPIN strength analysis are shown alongside a copyable session report.

---

## Security Rating System

BruteBox observes the following signals during the attack and generates a rating:

| Signal | Points |
|---|---|
| Rate limiting detected (HTTP 429) | +2 |
| IP or account blocking detected | +3 |
| CSRF token rotation (5+ rotations) | +2 |
| Basic CSRF protection present | +1 |
| Intentional response delay (>2s avg) | +2 |
| Moderate response delay (>1s avg) | +1 |

| Score | Rating |
|---|---|
| 0-1 | WEAK |
| 2-3 | MODERATE |
| 4-5 | STRONG |
| 6+ | FORTRESS |

---

## Project Structure
```
BruteBox/
├── app.py                  # Flask backend, attack engine, Socket.IO handlers
├── requirements.txt        # Python dependencies
├── templates/
│   └── index.html          # Frontend UI
└── README.md
```

---

## Disclaimer

This tool is intended **strictly for authorized security testing**. You must own the target system or have explicit written permission before running any attack. Unauthorized use against systems you do not own is illegal under applicable computer crime and cybersecurity laws in your jurisdiction. The author assumes no liability for misuse.

---

## Author

Built by [Sreyas](https://github.com/codebysreyas) · [LinkedIn](https://linkedin.com/in/sreyasvm)
