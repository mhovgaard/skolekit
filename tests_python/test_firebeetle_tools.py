"""Credential isolation and safe setup behavior of the USB device helper."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import termios
import threading
import shutil
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def helper(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'firmware/device.py'
    spec = importlib.util.spec_from_file_location('firebeetle_device', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    private = tmp_path / 'devices/firebeetle'
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'DEVICES', private.parent)
    monkeypatch.setattr(module, 'PRIVATE', private)
    monkeypatch.setattr(module, 'CONFIG', private / 'device.json')
    monkeypatch.setattr(module, 'CERT', private / 'device-cert.pem')
    controller = private.parent / 'local-controller.json'
    controller.parent.mkdir(parents=True)
    controller.write_text(json.dumps({'base_url': 'https://example.gateway.dev',
                                     'gateway_key': 'gateway-secret', 'token': 'controller-secret'}))
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='project', returncode=0))
    return module


def test_device_setup_preserves_other_devices_and_restricts_new_token(helper, monkeypatch, capsys):
    existing = [{'device_id': 'controller', 'token_hash': 'a' * 64, 'scopes': ['refresh', 'display']}]
    calls = []
    def gc(project, *parts, payload=None):
        calls.append((parts, payload))
        if parts[:3] == ('secrets', 'versions', 'access'):
            return json.dumps(existing).encode()
        return b'{}'
    monkeypatch.setattr(helper, 'gc', gc)
    helper.prepare(SimpleNamespace(project='project', region='europe-west1'))
    config = json.loads(helper.CONFIG.read_text())
    added = [json.loads(payload) for parts, payload in calls if parts[:3] == ('secrets', 'versions', 'add')][0]
    assert added[0] == existing[0]
    assert added[1] == {'device_id': 'firebeetle-ag', 'scopes': ['display'],
                        'token_hash': hashlib.sha256(config['token'].encode()).hexdigest()}
    assert config['token'] != 'controller-secret'
    assert helper.CONFIG.stat().st_mode & 0o777 == 0o600
    assert helper.CONFIG.parent.stat().st_mode & 0o777 == 0o700
    output = capsys.readouterr().out
    for value in (config['token'], config['gateway_key'], config['admin_password'], config['server_key']):
        assert value not in output
    reloads = [parts for parts, _ in calls if parts[:3] == ('run', 'services', 'update')]
    assert len(reloads) == 1
    assert not any('jobs' in parts or 'scheduler' in parts for parts, _ in calls)


def test_device_setup_refuses_existing_identity_with_different_token(helper, monkeypatch):
    calls = []
    def gc(project, *parts, payload=None):
        calls.append(parts)
        return json.dumps([{'device_id': 'firebeetle-ag', 'token_hash': 'b' * 64, 'scopes': ['display']}]).encode()
    monkeypatch.setattr(helper, 'gc', gc)
    with pytest.raises(ValueError, match='refusing overwrite'):
        helper.prepare(SimpleNamespace(project='project', region='europe-west1'))
    assert not any(parts[:3] in (('secrets', 'versions', 'add'), ('run', 'services', 'update')) for parts in calls)


def test_device_setup_detects_registry_change_before_write(helper, monkeypatch):
    calls = []
    def gc(project, *parts, payload=None):
        calls.append(parts)
        return b'[]' if len(calls) == 1 else b'[{"device_id":"concurrent"}]'
    monkeypatch.setattr(helper, 'gc', gc)
    with pytest.raises(ValueError, match='Registry changed'):
        helper.prepare(SimpleNamespace(project='project', region='europe-west1'))
    assert len(calls) == 2


def test_device_certificate_is_scoped_to_the_device_not_a_ca(helper, monkeypatch):
    from cryptography import x509
    monkeypatch.setattr(helper, 'gc', lambda *a, **k: b'[]')
    helper.prepare(SimpleNamespace(project='project', region='europe-west1'))
    cert = x509.load_pem_x509_certificate(helper.CERT.read_bytes())
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName) == ['firebeetle-ag.local']


def test_device_setup_finds_the_gateway_without_a_controller_file(helper, monkeypatch):
    (helper.PRIVATE.parent / 'local-controller.json').unlink()
    monkeypatch.setattr(helper, 'stored_gateway_key', lambda: 'keychain-gateway-key')
    calls = []
    def gc(project, *parts, payload=None):
        calls.append(parts)
        if parts[:3] == ('api-gateway', 'gateways', 'describe'):
            return b'agskoleintra-delivery-example.ew.gateway.dev\n'
        return b'[]'
    monkeypatch.setattr(helper, 'gc', gc)
    helper.prepare(SimpleNamespace(project='project', region='europe-north1'))
    config = json.loads(helper.CONFIG.read_text())
    assert config['gateway_host'] == 'agskoleintra-delivery-example.ew.gateway.dev'
    assert config['gateway_key'] == 'keychain-gateway-key'
    assert ('api-gateway', 'gateways', 'describe', 'agskoleintra-delivery', '--location=europe-north1',
            '--format=value(defaultHostname)') in calls
    assert any(parts[:3] == ('run', 'services', 'update') and '--region=europe-north1' in parts for parts in calls)


def test_device_setup_without_a_gateway_key_stops_before_registering(helper, monkeypatch):
    (helper.PRIVATE.parent / 'local-controller.json').unlink()
    monkeypatch.setattr(helper, 'stored_gateway_key', lambda: None)
    calls = []
    def gc(project, *parts, payload=None):
        calls.append(parts)
        return b'agskoleintra-delivery-example.ew.gateway.dev'
    monkeypatch.setattr(helper, 'gc', gc)
    with pytest.raises(ValueError, match='deploy the gateway first'):
        helper.prepare(SimpleNamespace(project='project', region='europe-west1'))
    assert not any(parts[0] in ('secrets', 'run') for parts in calls)
    assert not helper.CONFIG.exists()


@pytest.mark.parametrize('origin', ['http://example.gateway.dev', 'https://example.com',
                                   'https://example.gateway.dev/other', 'https://user:pass@example.gateway.dev'])
def test_device_setup_rejects_unexpected_gateway_origin(helper, monkeypatch, origin):
    controller = helper.PRIVATE.parent / 'local-controller.json'
    controller.write_text(json.dumps({'base_url': origin, 'gateway_key': 'secret'}))
    monkeypatch.setattr(helper, 'gc', lambda *a, **k: pytest.fail('No GCP operation permitted'))
    with pytest.raises(ValueError, match='HTTPS API Gateway origin'):
        helper.prepare(SimpleNamespace(project='project', region='europe-west1'))


def test_usb_handshake_recovers_from_partial_startup_line_and_disables_echo(helper):
    master, slave = pty.openpty()
    device = helper.Serial(os.ttyname(slave))
    os.close(slave)
    assert not termios.tcgetattr(device.fd)[3] & termios.ECHO
    def board():
        pending = b''
        hellos = 0
        while hellos < 2:
            pending += os.read(master, 1024)
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                if line == b'hello':
                    hellos += 1
                    os.write(master, b'partial startup line\r\n' if hellos == 1 else b'{"protocol":"ag-cloud-1"}\r\n')
    worker = threading.Thread(target=board, daemon=True)
    worker.start()
    try:
        assert device.hello() == {'protocol': 'ag-cloud-1'}
        worker.join(timeout=2)
        assert not worker.is_alive()
    finally:
        device.close()
        os.close(master)


def test_friendly_embedded_units_and_copenhagen_daylight_saving(tmp_path):
    compiler = shutil.which('c++')
    assert compiler, 'A C++ compiler is needed to verify the firmware status formatter'
    root = Path(__file__).resolve().parents[1]
    source = root / 'firmware/firebeetle-cloud'
    binary = tmp_path / 'friendly-status-test'
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(source),
                    str(source / 'friendly_status.cpp'), str(root / 'firmware/tests/friendly_status_test.cpp'),
                    '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)


def test_cli_uses_device_formatted_labels_and_units(helper):
    status = {'uptime_ms': 9000, 'readable': [
        {'section': 'Status', 'label': 'Device', 'value': 'Running — responding now'},
        {'section': 'Status', 'label': 'Time running since restart', 'value': '9 seconds'},
        {'section': 'Technical details', 'label': 'Available memory', 'value': '171.0 kB'},
    ]}
    text = helper.format_status(status, details=True)
    assert 'Device: Running — responding now' in text
    assert 'Time running since restart: 9 seconds' in text
    assert 'Available memory: 171.0 kB' in text
    assert 'uptime_ms' not in text and '9000' not in text
    assert 'Available memory' not in helper.format_status(status)


def test_frame_validation_rejects_truncation_extra_data_corruption_and_invalid_colours(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = root / 'firmware/firebeetle-cloud'
    binary = tmp_path / 'frame-validator-test'
    subprocess.run([shutil.which('c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(source),
                    str(root / 'firmware/tests/frame_validator_test.cpp'), '-o', str(binary)],
                   check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)


def test_sync_history_preserves_completed_metrics_and_records_failures(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / 'sync-history-test'
    subprocess.run([shutil.which('c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-I', str(root / 'firmware/firebeetle-cloud'),
                    str(root / 'firmware/tests/sync_history_test.cpp'), '-o', str(binary)],
                   check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)


def test_operational_cli_indicators_and_numbering(helper):
    text = helper.format_status({'readable': [
        {'section': 'Status', 'label': 'Device', 'value': 'Online', 'state': 'ok'},
        {'section': 'Status', 'label': 'Wi-Fi', 'value': 'Disconnected', 'state': 'error'},
        {'section': 'Status', 'label': 'Display', 'value': 'Not connected', 'state': 'neutral'},
        {'section': 'Last sync', 'label': 'Download speed', 'value': '32.0 kB/s', 'state': 'neutral'},
    ]})
    assert '01 Device: [OK] Online' in text
    assert '02 Wi-Fi: [FAIL] Disconnected' in text
    assert '03 Display: [—] Not connected' in text
    assert 'Download speed: [—] 32.0 kB/s' in text


@pytest.mark.parametrize('name', ['wake_plan', 'battery_level'])
def test_wake_schedule_and_battery_estimates(tmp_path, name):
    root = Path(__file__).resolve().parents[1]
    source = root / 'firmware/firebeetle-cloud'
    binary = tmp_path / f'{name}-test'
    subprocess.run([shutil.which('c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(source),
                    str(source / 'friendly_status.cpp'), str(root / f'firmware/tests/{name}_test.cpp'),
                    '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)


def fake_board(master, replies, last):
    """Answer each USB line the helper sends with the matching JSON reply, until `last`."""
    pending = b''
    while True:
        pending += os.read(master, 1024)
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            command = line.decode().strip()
            for prefix, reply in replies.items():
                if command.startswith(prefix):
                    os.write(master, json.dumps(reply).encode() + b'\r\n')
            if command == last:
                return


def test_power_command_sets_mode_and_reports_the_power_rows(helper, capsys):
    master, slave = pty.openpty()
    replies = {'hello': {'protocol': 'ag-cloud-1', 'readable_status': True, 'power': True},
               'time ': {'clock_set': True}, 'power sleep': {'power_mode': 'sleep'},
               'status-json': {'last_result': 'ok', 'readable': [
                   {'section': 'Status', 'label': 'Battery', 'value': '3.98 V · about 70%', 'state': 'ok'},
                   {'section': 'Power', 'label': 'Mode', 'value': 'Sleeps between updates', 'state': 'neutral'}]}}
    worker = threading.Thread(target=fake_board, args=(master, replies, 'status-json'), daemon=True)
    worker.start()
    port = os.ttyname(slave)
    # Keep this end open until the helper is done: on Linux, reading the board's end
    # fails with EIO while no process holds the port open (macOS waits instead).
    try:
        helper.usb_command(SimpleNamespace(port=port, command='power', mode='sleep', details=False))
        worker.join(timeout=2)
        assert not worker.is_alive()
    finally:
        os.close(master)
        os.close(slave)
    text = capsys.readouterr().out
    assert '01 Battery: [OK] 3.98 V · about 70%' in text
    assert 'Mode: [—] Sleeps between updates' in text


def test_watch_logs_wakes_syncs_and_sleeps(helper, capsys):
    master, slave = pty.openpty()
    def board():
        for event in ({'firmware': 'ag-cloud-0.6.0', 'ready': True, 'wake': 'timer', 'power_mode': 'sleep'},
                      {'cycle': 'board_unchanged', 'battery_mv': 3981, 'duration_ms': 6400},
                      {'sleep_seconds': 180, 'wake_reason': 'test_interval', 'awake_ms': 9100},
                      {'sleep_seconds': 21000, 'timer_seconds': 21512, 'wake_reason': 'scan_published',
                       'awake_ms': 17100}):
            os.write(master, json.dumps(event).encode() + b'\r\n')
    threading.Thread(target=board, daemon=True).start()
    try:
        helper.watch(SimpleNamespace(port=os.ttyname(slave), minutes=0.03))
    finally:
        os.close(master)
        os.close(slave)
    text = capsys.readouterr().out
    assert 'awake: timer · sleep mode · ag-cloud-0.6.0' in text
    assert 'sync: board unchanged · battery 3.98 V · 6.4 s' in text
    assert 'sleep: 180 s until' in text and '(test interval) · awake 9.1 s' in text
    assert '(scan published) · timer 21512 s · awake 17.1 s' in text
