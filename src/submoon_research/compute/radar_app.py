"""Локальный интерфейс и жизненный цикл радара: браузер + Windows tray, без аренды."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import threading
import time
import webbrowser

from submoon_research.compute.radar import (
    CONFIG, FINDINGS, OUTPUT, ROOT, AlreadyRunning, Radar, Store, load_config, save_json, single_instance, utc,
)


README = 'docs/compute/vast_radar_guide.md'


def play_sound():
    if os.name == 'nt':
        import winsound
        winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


def control(radar, action):
    if action == 'check':
        radar.wake.set()
    elif action == 'pause':
        radar.store.put('paused', not radar.store.get('paused', False))
        radar.wake.set()
    elif action == 'sound':
        radar.store.put('sound_enabled', not radar.store.get('sound_enabled', radar.cfg['sound_enabled']))
    elif action == 'snooze':
        radar.store.put('snooze_until', radar.wall()+3600)
    elif action == 'test_sound':
        radar.notify()
    elif action == 'ack':
        events = radar.store.recent_events()
        radar.store.put('ack_event_id', events[0]['event_id'] if events else 0)
    elif action == 'stop':
        radar.stop.set()
    else:
        raise ValueError('Неизвестная команда')
    radar.publish()


def make_server(radar, port):
    token = secrets.token_urlsafe(32)
    expected_host = f'127.0.0.1:{port}'
    origin = f'http://{expected_host}'
    template = Path(__file__).with_name('radar.html').read_text(encoding='utf-8')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, status, body, content='application/json; charset=utf-8'):
            data = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):
            return self.headers.get('Host') == expected_host

        def do_GET(self):
            if not self.host_ok():
                return self.respond(403, '{}')
            if self.path == '/':
                return self.respond(200, template.replace('__TOKEN__', token), 'text/html; charset=utf-8')
            if self.path == '/api/state':
                data = radar.view()
                data['ack_event_id'] = radar.store.get('ack_event_id', 0)
                return self.respond(200, json.dumps(data, ensure_ascii=False, allow_nan=False))
            return self.respond(404, '{}')

        def do_POST(self):
            # Локальная страница не даёт стороннему сайту управлять монитором через CSRF/rebinding.
            if (not self.host_ok() or self.path != '/control'
                    or self.headers.get('Origin') not in (None, origin)
                    or not secrets.compare_digest(self.headers.get('X-Radar-Token', ''), token)
                    or self.headers.get_content_type() != 'application/json'):
                return self.respond(403, '{}')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024:
                    return self.respond(400, '{}')
                value = json.loads(self.rfile.read(size))
                control(radar, value['action'])
            except (ValueError, KeyError, TypeError):
                return self.respond(400, '{}')
            return self.respond(200, '{"ok":true}')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def icon_color(data):
    if data['paused']:
        return '#888888'
    if any(r['state'] == 'available' and not r['stale'] for r in data['rows']):
        return '#28ad68'
    last = data['status']['last_success_epoch']
    if data['status']['errors'] or data['review_required'] or (last and time.time()-last > 1800):
        return '#e3ab34'
    return '#357bdd'


def create_tray(radar, url):
    import pystray
    from PIL import Image, ImageDraw

    def image(color):
        result = Image.new('RGBA', (64, 64))
        draw = ImageDraw.Draw(result)
        draw.ellipse((5, 5, 59, 59), fill=color)
        draw.line((32, 32, 50, 14), fill='white', width=5)
        draw.ellipse((26, 26, 38, 38), fill='white')
        return result

    def callback(action):
        return lambda *_args: control(radar, action)

    tray = pystray.Icon('submoon-vast-radar', image('#357bdd'), 'Submoon: радар станций', menu=pystray.Menu(
        pystray.MenuItem('Открыть радар', lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem('Проверить сейчас', callback('check')),
        pystray.MenuItem('Пауза', callback('pause'), checked=lambda _: radar.store.get('paused', False)),
        pystray.MenuItem('Звук', callback('sound'), checked=lambda _: radar.store.get('sound_enabled', radar.cfg['sound_enabled'])),
        pystray.MenuItem('Без звука на час', callback('snooze')),
        pystray.MenuItem('Проверить звук', callback('test_sound')),
        pystray.MenuItem('Завершить', callback('stop'))))
    return tray, image


def runtime(radar, active):
    from submoon_research.tracking import writer_lock
    path = ROOT/'tracking/runtime.json'
    with writer_lock(ROOT):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        data['processes'] = [p for p in data['processes'] if p.get('radar_id') != 'vast-radar']
        if active:
            data['processes'].append(dict(radar_id='vast-radar', run_id='vast-radar',
                kind='read_only_market_monitor', task_id='W2-T026', owner='pi-vast-radar-20261015',
                pid=os.getpid(), last_checked_utc=utc(), paid_actions=0,
                heartbeat='data/interim/vast_radar/heartbeat.json',
                command='scripts/vast_radar.py --desktop', stop='меню трея / локальная страница: Завершить'))
        data['updated_utc'] = utc()
        save_json(path, data)


def serve(radar, *, tray_enabled=True, open_page=False, register_runtime=True):
    port = radar.cfg['port']
    url = f'http://127.0.0.1:{port}/'
    server = make_server(radar, port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    worker = threading.Thread(target=radar.run, name='vast-radar-collector', daemon=True)
    tray, image = None, None
    if tray_enabled:
        try:
            tray, image = create_tray(radar, url)
        except (ImportError, OSError):
            logging.warning('Tray unavailable; local dashboard remains active')
    save_json(radar.store.folder/'launch.json', dict(pid=os.getpid(), started_utc=utc(), url=url,
                                                   mode='read_only', automatic_rental=False))
    if register_runtime:
        runtime(radar, True)
    server_thread.start()
    worker.start()
    if open_page:
        webbrowser.open(url)

    def heartbeat():
        previous_color = None
        while not radar.stop.wait(2):
            data = radar.view()
            save_json(radar.store.folder/'heartbeat.json', dict(pid=os.getpid(), utc=utc(),
                running=True, polling=data['status']['polling'], last_cycle_utc=data['status']['last_cycle_utc'],
                next_poll_epoch=data['status']['next_poll_epoch'], errors=data['status']['errors'],
                paused=data['paused'], review_required=data['review_required'], paid_actions=0))
            if tray and image is not None:
                color = icon_color(data)
                if color != previous_color:
                    tray.icon = image(color)
                    previous_color = color
            if not worker.is_alive():
                logging.error('Collector stopped unexpectedly')
                radar.stop.set()
        if tray:
            tray.stop()

    ticker = threading.Thread(target=heartbeat, daemon=True)
    ticker.start()
    try:
        if tray:
            tray.run()
        else:
            while not radar.stop.wait(1):
                pass
    except KeyboardInterrupt:
        radar.stop.set()
    finally:
        radar.stop.set()
        server.shutdown()
        server.server_close()
        worker.join(180)
        ticker.join(5)
        save_json(radar.store.folder/'heartbeat.json', dict(pid=os.getpid(), utc=utc(), running=False, paid_actions=0))
        if register_runtime:
            runtime(radar, False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--once', action='store_true', help='Один бесплатный цикл без трея/сервера')
    parser.add_argument('--status', action='store_true', help='Прочитать сохранённый результат, без сети')
    parser.add_argument('--desktop', action='store_true', help='Трей и локальный сервер; без открытия браузера')
    parser.add_argument('--open', action='store_true', help='Открыть страницу (не запустить второй сборщик)')
    parser.add_argument('--no-tray', action='store_true')
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{cfg['port']}/")
        return 0
    if args.status:
        print((OUTPUT/'latest.json').read_text(encoding='utf-8'))
        return 0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
        OUTPUT/'radar.log', maxBytes=1024*1024, backupCount=3, encoding='utf-8')])
    try:
        with single_instance(OUTPUT):
            store = Store(OUTPUT)
            radar = Radar(cfg, store, FINDINGS, notify=play_sound)
            try:
                if args.once:
                    radar.poll()
                    radar.status['running'] = False
                    radar.publish()
                else:
                    serve(radar, tray_enabled=not args.no_tray, open_page=not args.desktop)
            finally:
                store.close()
    except AlreadyRunning:
        # Повторный запуск открывает существующий интерфейс, а не плодит процессы.
        webbrowser.open(f"http://127.0.0.1:{cfg['port']}/")
        return 0
    except Exception as exc:
        logging.error('Radar failed: %s', type(exc).__name__)
        raise
    return 0
