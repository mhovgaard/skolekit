#!/usr/bin/env python3
"""Private setup and USB control for the FireBeetle cloud sketch.

Use the repository .venv/bin/python (httpx and cryptography are required for
setup/HTTPS checks). USB control itself uses the Python standard library.
Secrets are never accepted as command-line arguments or printed on failure.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import select
import ssl
import subprocess
import sys
import termios
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / '.ag-school' / 'devices' / 'firebeetle'
CONFIG = PRIVATE / 'device.json'
CERT = PRIVATE / 'device-cert.pem'
DEVICE = 'firebeetle-ag'


def private_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def load_config() -> dict:
    if not CONFIG.exists():
        raise ValueError('Run the setup command first.')
    return json.loads(CONFIG.read_text())


def gc(project: str, *parts: str, payload: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ['gcloud', *parts, '--project=' + project, '--quiet'],
        input=payload, capture_output=True,
    )
    if completed.returncode:
        # Provider errors can contain payloads. Never forward their raw output.
        raise RuntimeError('GCP operation failed: ' + ' '.join(parts[:2]))
    return completed.stdout


def stored_gateway_key() -> str | None:
    """The gateway API key that deploy-gateway.sh keeps in the Keychain."""
    from skoleklar.device_token import stored_gateway_api_key
    return stored_gateway_api_key()


def gateway_access(project: str, region: str) -> tuple[str, str]:
    """The gateway origin and key for the board.

    An older `local-controller.json` may name both. Otherwise the origin is the
    deployed gateway's own hostname and the key is the one in the Keychain.
    """
    path = ROOT / '.ag-school/devices/local-controller.json'
    controller = json.loads(path.read_text()) if path.exists() else {}
    base_url = controller.get('base_url')
    if not base_url:
        host = gc(project, 'api-gateway', 'gateways', 'describe', 'agskoleintra-delivery',
                  '--location=' + region, '--format=value(defaultHostname)').decode().strip()
        base_url = 'https://' + host
    gateway_key = controller.get('gateway_key') or stored_gateway_key()
    if not gateway_key:
        raise ValueError('No gateway API key; deploy the gateway first (infra/gcp/deploy-gateway.sh).')
    return base_url, gateway_key


def prepare(args) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    active = subprocess.run(['gcloud', 'config', 'get-value', 'project'],
                            capture_output=True, text=True, check=True).stdout.strip()
    if active != args.project:
        raise ValueError('The requested GCP project must already be active.')
    base_url, gateway_key = gateway_access(args.project, args.region)
    origin = urlparse(base_url)
    if (origin.scheme != 'https' or not origin.hostname or not origin.hostname.endswith('.gateway.dev')
            or origin.username or origin.password or origin.port not in (None, 443)
            or origin.path not in ('', '/') or origin.query or origin.fragment):
        raise ValueError('Expected an HTTPS API Gateway origin (https://NAME.gateway.dev).')
    config = load_config() if CONFIG.exists() else {
        'device_id': DEVICE, 'token': secrets.token_urlsafe(48),
        'admin_password': secrets.token_urlsafe(24),
    }
    if config['device_id'] != DEVICE:
        raise ValueError('Unexpected local device identity; explicit reconciliation required.')
    config.update(gateway_host=origin.hostname, gateway_key=gateway_key)
    if 'server_cert' not in config:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, DEVICE + '.local')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(DEVICE + '.local')]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(key, hashes.SHA256()))
        config['server_cert'] = cert.public_bytes(serialization.Encoding.PEM).decode()
        config['server_key'] = key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    private_write(CONFIG, json.dumps(config, indent=2) + '\n')
    private_write(CERT, config['server_cert'])
    identifier = 'agskoleintra-device-registry'
    before = gc(args.project, 'secrets', 'versions', 'access', 'latest', '--secret=' + identifier)
    registry = json.loads(before)
    if not isinstance(registry, list):
        raise ValueError('Unexpected device registry format.')
    entry = {'device_id': DEVICE, 'token_hash': hashlib.sha256(config['token'].encode()).hexdigest(),
             'scopes': ['display']}
    matches = [item for item in registry if item['device_id'] == DEVICE]
    if matches and matches != [entry]:
        raise ValueError('Device identity already has different credentials or scopes; refusing overwrite.')
    if not matches:
        # Preserve every existing credential and detect changes during preparation.
        current = gc(args.project, 'secrets', 'versions', 'access', 'latest', '--secret=' + identifier)
        if current != before:
            raise ValueError('Registry changed during setup; rerun to reconcile.')
        registry.append(entry)
        gc(args.project, 'secrets', 'versions', 'add', identifier, '--data-file=-',
           payload=json.dumps(registry).encode())
    # Existing containers load registry at startup. Roll the same service image;
    # preserve unrelated environment, identity, ingress and scaling settings.
    gc(args.project, 'run', 'services', 'update', 'agskoleintra-delivery',
       '--region=' + args.region, '--update-secrets=AG_DEVICE_REGISTRY=' + identifier + ':latest')
    print('Display-only credential registered; delivery service reloaded. Existing device entries preserved.')
    print('Private device setup saved. No school refresh or schedule was started.')
    fingerprint(config)


def fingerprint(config: dict) -> None:
    der = ssl.PEM_cert_to_DER_cert(config['server_cert'])
    print('Device certificate SHA-256:', hashlib.sha256(der).hexdigest())
    print('Certificate:', CERT)


class Serial:
    def __init__(self, port: str):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            attr = termios.tcgetattr(self.fd)
            attr[0] = 0
            attr[1] = 0
            attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attr[3] = 0  # No local echo: provisioning lines contain credentials.
            attr[4] = attr[5] = termios.B115200
            attr[6][termios.VMIN] = 0
            attr[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attr)
            self.buffer = b''
            self.capabilities = {}
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def send(self, value: str):
        remaining = (value + '\n').encode()
        deadline = time.monotonic() + 10
        while remaining:
            if time.monotonic() >= deadline:
                raise TimeoutError('USB write timed out.')
            if select.select([], [self.fd], [], .5)[1]:
                remaining = remaining[os.write(self.fd, remaining):]

    def receive(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    data = json.loads(line.strip())
                except (ValueError, UnicodeError):
                    continue
                if predicate(data):
                    return data
            if select.select([self.fd], [], [], .2)[0]:
                self.buffer += os.read(self.fd, 16384)
                if len(self.buffer) > 32768:
                    raise ValueError('Unexpected oversized USB response.')
        raise TimeoutError('No matching response from the cloud firmware over USB.')

    def hello(self):
        # A port can open in the middle of a boot/log line. Synchronize with a
        # newline and retry the harmless handshake instead of losing its first
        # response to a partial line or a board still starting up.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.send('\nhello')
            try:
                self.capabilities = self.receive(lambda d: d.get('protocol') == 'ag-cloud-1', timeout=1)
                return self.capabilities
            except TimeoutError:
                pass
        raise TimeoutError('No cloud firmware handshake over USB. Close other serial monitors and check the upload.')

    def status(self):
        self.send('status-json' if self.capabilities.get('readable_status') else 'status')
        return self.receive(lambda d: 'last_result' in d)


@contextmanager
def serial_connection(port):
    connection = Serial(port)
    try:
        yield connection
    finally:
        connection.close()


def provision(args):
    config = load_config()
    if args.wifi_from_keychain:
        found = subprocess.run(['security', 'find-generic-password', '-D', 'AirPort network password',
                                '-a', args.ssid, '-w'], capture_output=True)
        if found.returncode:
            raise ValueError('Saved Wi-Fi password unavailable. Run provision without --wifi-from-keychain for a private prompt.')
        wifi_password = found.stdout.decode().rstrip('\n')
    else:
        wifi_password = getpass.getpass('Wi-Fi password (hidden): ')
    if not 1 <= len(args.ssid.encode()) <= 32 or not 8 <= len(wifi_password.encode()) <= 63:
        raise ValueError('This build requires a 1-32 byte SSID and 8-63 byte Wi-Fi password.')
    header = 'Basic ' + base64.b64encode(('admin:' + config['admin_password']).encode()).decode()
    payload = {key: config[key] for key in ('gateway_host', 'gateway_key', 'token', 'server_cert', 'server_key')}
    payload.update(ssid=args.ssid, wifi_password=wifi_password,
                   admin_auth_hash=hashlib.sha256(header.encode()).hexdigest())
    encoded = json.dumps(payload, separators=(',', ':'), ensure_ascii=True)
    if len(encoded.encode()) > 8192:
        raise ValueError('Provisioning payload exceeds the firmware bound.')
    with serial_connection(args.port) as connection:
        connection.hello()
        connection.send('provision ' + encoded)
        response = connection.receive(lambda d: 'provisioned' in d or 'error' in d)
        if not response.get('provisioned'):
            raise ValueError('Firmware rejected provisioning; no secret details were returned.')
        print('Provisioned. Waiting for household Wi-Fi...')
        time.sleep(2)
        connection.send('time ' + str(int(time.time())))
        connection.receive(lambda d: d.get('clock_set'))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status = connection.status()
            if status.get('wifi_connected') and status.get('page', '').startswith('https:'):
                print(format_status(status))
                fingerprint(config)
                return
            time.sleep(1)
        raise TimeoutError('Provisioning saved, but Wi-Fi or maintenance server did not become ready. Check status over USB.')


def format_status(status: dict, *, details: bool = False) -> str:
    """Use the device's own labels/units, shared with its HTML and serial page."""
    rows = status.get('readable')
    if not isinstance(rows, list):
        return json.dumps(status, indent=2)  # Compatibility with the first build.
    lines = ['AG School device']
    previous = None
    step = 0
    for row in rows:
        if row['section'] == 'Technical details' and not details:
            continue
        if row['section'] != previous:
            lines.extend(['', row['section']])
            previous = row['section']
        tone = row.get('state')
        badge = {'ok': 'OK', 'warn': 'WAIT', 'error': 'FAIL', 'neutral': '—'}.get(tone)
        prefix = ''
        if tone and row['section'] == 'Status':
            step += 1
            prefix = f'{step:02d} '
        lines.append(f"{prefix}{row['label']}: " + (f'[{badge}] ' if badge else '') + row['value'])
    lines.extend(['', 'Times: Copenhagen (CET/CEST). History is saved across restarts.'])
    lines.append('Memory: kB = 1,000 bytes.' if details else 'Add --details for technical readings.')
    return '\n'.join(lines)


def usb_command(args):
    with serial_connection(args.port) as connection:
        connection.hello()
        if args.command == 'scan':
            connection.send('scan')
            status = connection.receive(lambda d: 'configured_network_visible' in d or 'error' in d, timeout=25)
        elif args.command == 'ssid':
            if not 1 <= len(args.ssid.encode()) <= 32 or '\n' in args.ssid or '\r' in args.ssid:
                raise ValueError('Invalid network name.')
            connection.send('ssid ' + args.ssid)
            status = connection.receive(lambda d: 'ssid_updated' in d or 'error' in d)
        elif args.command in ('check', 'sync', 'frame-test', 'frame-readback', 'draw'):
            if args.command != 'check' and not connection.capabilities.get('frame_cache'):
                raise ValueError('The connected firmware does not yet support frame caching.')
            if args.command == 'draw' and not connection.capabilities.get('display'):
                raise ValueError('The connected firmware does not yet support drawing on the display.')
            connection.send('time ' + str(int(time.time())))
            connection.receive(lambda d: d.get('clock_set'))
            connection.send(args.command + '-json' if connection.capabilities.get('readable_status') else args.command)
            queued = connection.receive(lambda d: 'check' in d or 'error' in d)
            if queued.get('error'):
                raise ValueError('A check is running or in its ten-second cooldown.')
            status = connection.receive(lambda d: 'last_result' in d and not d.get('check_busy') and not d.get('frame_busy'),
                                        timeout=180 if args.command != 'check' else 45)
        elif args.command in POWER_REPLIES:
            if not connection.capabilities.get('power'):
                raise ValueError('The connected firmware does not yet support power control.')
            line = {'power': f'power {getattr(args, "mode", "")}',
                    'wake-test': f'wake-test {getattr(args, "minutes", 0)}',
                    'battery-calibrate': f'battery-calibrate {getattr(args, "millivolts", 0)}'}.get(args.command, args.command)
            if args.command != 'battery':
                connection.send('time ' + str(int(time.time())))
                connection.receive(lambda d: d.get('clock_set'))
            connection.send(line)
            key = POWER_REPLIES[args.command]
            reply = connection.receive(lambda d: key in d or 'error' in d)
            if reply.get('error'):
                raise ValueError({'invalid_power_mode': 'Unknown power mode.',
                                  'invalid_minutes': 'Use 0 to 720 minutes.',
                                  'calibration_rejected': 'No battery reading to calibrate, or the correction is above 20%.',
                                  'not_in_sleep_mode': 'Switch to sleep mode first: power sleep.'}.get(reply['error'], reply['error']))
            if args.command == 'battery':
                print(json.dumps(reply, indent=2))
                return
            if args.command == 'sleep-now':
                print('Sleeping until the next planned wake. Press the board button to wake it now.')
                return
            status = connection.status()
        else:
            status = connection.status()
        print(json.dumps(status, indent=2) if getattr(args, 'json', False)
              else format_status(status, details=getattr(args, 'details', False)))
        if args.command == 'check' and not status['last_check_authenticated']:
            raise SystemExit(1)
        if args.command in ('sync', 'frame-test', 'frame-readback') and (status.get('frame_last_result') != 'ok'
                or not status.get('frame_ready') or not status.get('frame_readback_verified')):
            raise SystemExit(1)
        if args.command == 'draw' and status.get('display_result') != 'ok':
            raise SystemExit(1)


POWER_REPLIES = {'power': 'power_mode', 'wake-test': 'wake_test_minutes',
                 'battery-calibrate': 'battery_scale_permille', 'battery': 'battery_present',
                 'sleep-now': 'sleeping'}


def watch(args):
    """Print each start, sync cycle, button press and sleep the board reports, with the Mac's time."""
    deadline = time.monotonic() + args.minutes * 60
    with serial_connection(args.port) as connection:
        print(f'Watching {args.port} for {args.minutes} minutes. Ctrl+C stops.')
        while time.monotonic() < deadline:
            try:
                event = connection.receive(lambda d: any(k in d for k in ('ready', 'cycle', 'button', 'sleep_seconds')),
                                           timeout=max(1, deadline - time.monotonic()))
            except TimeoutError:
                break
            stamp = datetime.now().strftime('%H:%M:%S')
            if 'ready' in event:
                print(f"{stamp}  awake: {event.get('wake', 'start')} · {event.get('power_mode', '?')} mode · {event.get('firmware')}")
            elif 'cycle' in event:
                report = f" · cloud report {event['report'].replace('_', ' ')}" if 'report' in event else ''
                print(f"{stamp}  sync: {event['cycle'].replace('_', ' ')} · battery {event['battery_mv'] / 1000:.2f} V"
                      f" · {event['duration_ms'] / 1000:.1f} s{report}")
            elif 'button' in event:
                print(f"{stamp}  button: staying awake 10 minutes")
            else:
                wake_at = datetime.now() + timedelta(seconds=event['sleep_seconds'])
                timer = event.get('timer_seconds', event['sleep_seconds'])
                corrected = f" · timer {timer} s" if timer != event['sleep_seconds'] else ''
                print(f"{stamp}  sleep: {event['sleep_seconds']} s until {wake_at:%H:%M:%S}"
                      f" ({event['wake_reason'].replace('_', ' ')}){corrected} · awake {event['awake_ms'] / 1000:.1f} s")


def cloud_test(args):
    import httpx
    config = load_config()
    headers = {'x-api-key': config['gateway_key'], 'Authorization': 'Bearer ' + config['token']}
    with httpx.Client(base_url='https://' + config['gateway_host'], timeout=30) as client:
        for label, request_headers, expected in (
            ('missing_bearer', {'x-api-key': config['gateway_key']}, 401),
            ('wrong_bearer', {**headers, 'Authorization': 'Bearer invalid'}, 401),
            ('missing_gateway_key', {'Authorization': headers['Authorization']}, 401),
            ('display_connection', headers, 200),
        ):
            response = client.get('/v1/display/status', headers=request_headers)
            print(f"{label.replace('_', ' ')}: HTTP {response.status_code}")
            if response.status_code != expected:
                raise ValueError('Cloud check did not return the expected status: ' + label)
            if label == 'display_connection':
                data = response.json()
                if data.get('ok') is not True or data.get('device_id') != DEVICE:
                    raise ValueError('Unexpected authenticated device identity.')
                print('Board in GCP: ' + ('Available' if data['publication_available'] else 'No current board available'))
        # Authorization fails before parsing or dispatch; no cloud job is started.
        response = client.post('/v1/refresh', headers=headers, json={})
        print(f"School refresh permission: HTTP {response.status_code} (403 means correctly denied)")
        if response.status_code != 403:
            raise ValueError('Display-only credential isolation was not confirmed.')


def web_test(args):
    import httpx
    config = load_config()
    context = ssl.create_default_context(cafile=str(CERT))
    # Optionally route to the USB-reported address while preserving TLS hostname
    # verification. The supplied IP never replaces the authenticated host name.
    transport = None
    url = 'https://' + DEVICE + '.local'
    if args.ip:
        ipaddress.ip_address(args.ip)
        url = 'https://' + args.ip
        transport = httpx.HTTPTransport(verify=context)
    with httpx.Client(base_url=url, verify=context, transport=transport,
                      timeout=15, trust_env=False) as client:
        extensions = {'sni_hostname': (DEVICE + '.local').encode()} if args.ip else {}
        response = client.get('/', extensions=extensions)
        if response.status_code != 401:
            raise ValueError('Unauthenticated page was not rejected.')
        auth = ('admin', config['admin_password'])
        for path in ('/', '/api/status'):
            response = client.get(path, auth=auth, extensions=extensions)
            if response.status_code != 200:
                raise ValueError('Authenticated maintenance route failed: ' + path)
            if path == '/':
                if 'AG School device' not in response.text:
                    raise ValueError('Unexpected HTML page.')
                # Ensure the browser never receives cloud or admin credentials.
                for secret in (config['token'], config['gateway_key'], config['admin_password'], config['server_key']):
                    if secret in response.text:
                        raise ValueError('Private credential found in HTML.')
                private_write(PRIVATE / 'last-page.html', response.text)
            else:
                print(format_status(response.json()))
        for path in ('/sync', '/check', '/frame-test', '/frame-readback', '/draw'):
            response = client.post(path, content='csrf=' + '0' * 64, extensions=extensions)
            if response.status_code != 401:
                raise ValueError('Unauthenticated action was not rejected: ' + path)
            response = client.post(path, auth=auth, content='csrf=' + '0' * 64, extensions=extensions)
            if response.status_code != 403:
                raise ValueError('Invalid action token was not rejected: ' + path)
        print('HTTPS certificate, login, HTML, JSON and action-token checks passed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/cu.wchusbserial110')
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('setup', help='Register a dedicated display-only device and generate local HTTPS credentials')
    setup.add_argument('--project', required=True)
    setup.add_argument('--region', default='europe-west1', help='Where the gateway and delivery service run')
    provision_parser = commands.add_parser('provision', help='Send private settings over USB')
    provision_parser.add_argument('--ssid', required=True)
    provision_parser.add_argument('--wifi-from-keychain', action='store_true')
    status_parser = commands.add_parser('status', help='Read device status over USB in plain language')
    status_parser.add_argument('--json', action='store_true', help='Return raw diagnostics with explicit unit definitions')
    status_parser.add_argument('--details', action='store_true', help='Include memory, signal, response codes and board timestamps')
    check_parser = commands.add_parser('check', help='Request one GCP connection check over USB')
    check_parser.add_argument('--json', action='store_true', help='Return raw diagnostics with explicit unit definitions')
    check_parser.add_argument('--details', action='store_true', help='Include technical readings after the check')
    for command in ('sync', 'frame-test', 'frame-readback'):
        frame_parser = commands.add_parser(command, help='Sync the published board from GCP' if command != 'frame-readback'
                                           else 'Verify the saved frame without a cloud request')
        frame_parser.add_argument('--json', action='store_true', help='Return raw diagnostics with explicit units')
        frame_parser.add_argument('--details', action='store_true', help='Include checksums and storage measurements')
    draw_parser = commands.add_parser('draw', help='Redraw the saved board on the display without a cloud request')
    draw_parser.add_argument('--json', action='store_true', help='Return raw diagnostics with explicit units')
    draw_parser.add_argument('--details', action='store_true', help='Include panel timings and versions')
    power = commands.add_parser('power', help='Choose manual syncs, always-on automatic syncs, or sleep between updates')
    power.add_argument('mode', choices=('manual', 'awake', 'sleep'))
    power.add_argument('--details', action='store_true')
    wake_test = commands.add_parser('wake-test', help='Wake every N minutes instead of after each cloud scan; 0 restores the real schedule')
    wake_test.add_argument('minutes', type=int)
    wake_test.add_argument('--details', action='store_true')
    calibrate = commands.add_parser('battery-calibrate', help='Match the battery reading to a multimeter value in millivolts; 0 resets')
    calibrate.add_argument('millivolts', type=int)
    calibrate.add_argument('--details', action='store_true')
    commands.add_parser('battery', help='Read the battery voltage now')
    commands.add_parser('sleep-now', help='Sleep until the next planned wake (sleep mode only)')
    watcher = commands.add_parser('watch', help='Log each wake, sync and sleep reported over USB')
    watcher.add_argument('--minutes', type=float, default=30)
    commands.add_parser('scan', help='Diagnose visibility of the configured 2.4 GHz network over USB')
    network = commands.add_parser('ssid', help='Correct the network name while retaining the password on the board')
    network.add_argument('--ssid', required=True)
    commands.add_parser('cloud-test', help='Verify cloud authentication and scope isolation')
    web = commands.add_parser('web-test', help='Verify local HTTPS routes using the exact device certificate')
    web.add_argument('--ip', help='USB-reported LAN address; TLS still verifies firebeetle-ag.local')
    commands.add_parser('certificate', help='Show public certificate location and fingerprint')
    commands.add_parser('admin-copy', help='Copy the device admin password to the Mac clipboard')
    args = parser.parse_args()
    if args.command == 'setup': prepare(args)
    elif args.command == 'provision': provision(args)
    elif args.command in ('status', 'check', 'sync', 'scan', 'ssid', 'frame-test', 'frame-readback', 'draw', *POWER_REPLIES):
        usb_command(args)
    elif args.command == 'watch': watch(args)
    elif args.command == 'cloud-test': cloud_test(args)
    elif args.command == 'web-test': web_test(args)
    elif args.command == 'certificate': fingerprint(load_config())
    elif args.command == 'admin-copy':
        subprocess.run(['pbcopy'], input=load_config()['admin_password'].encode(), check=True)
        print('Device admin password copied. Username: admin. Clear the clipboard after use.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.CalledProcessError) as error:
        # Never include raw cloud, USB or certificate contents in tracebacks.
        print(str(error) if isinstance(error, (ValueError, RuntimeError, TimeoutError))
              else 'Operation failed; check device connection, local permissions and credentials.', file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        if type(error).__module__.startswith(('httpx', 'httpcore')):
            print('HTTPS connection failed. Check Wi-Fi, the device address and certificate trust.', file=sys.stderr)
            raise SystemExit(1) from None
        raise
