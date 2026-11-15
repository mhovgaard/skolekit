import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skoleklar.board_status import board_now, latest_record, render_page, sanitize_report
from skoleklar.browser_api import create_app as browser_app
from skoleklar.delivery_api import create_app
from skoleklar.delivery_store import LocalDeliveryStore
from tests_python.test_refresh_control import Dispatcher, MemoryStore, RefreshController

NOW = datetime(2026, 11, 3, 21, 40, tzinfo=UTC)


def report(**fields):
    value = {
        "firmware": "ag-cloud-0.7.0", "device_id": "firebeetle-ag", "power_mode": "sleep",
        "battery_present": True, "battery_mv": 3941, "battery_percent": 61, "battery_trend": "falling",
        "battery_samples": [{"at": "2026-11-03T12:00:00Z", "mv": 3990},
                            {"at": "2026-11-03T18:00:00Z", "mv": 3962},
                            {"at": "2026-11-03T21:39:00Z", "mv": 3941}],
        "awake_remaining_ms": 0, "next_wake_at": "2026-11-04T04:52:00Z", "next_wake_reason": "scan_published",
        "units": {"battery_mv": "millivolts"},
        "readable": [
            {"section": "Status", "label": "Device", "value": "Online · 6 seconds", "state": "ok"},
            {"section": "Status", "label": "Battery", "value": "3.94 V · about 61% · falling", "state": "ok"},
            {"section": "Power", "label": "Mode", "value": "Sleeps between updates", "state": "neutral"},
            {"section": "Technical details", "label": "Firmware", "value": "ag-cloud-0.7.0", "state": "neutral"},
        ],
    }
    value.update(fields)
    return value


def delivery(tmp_path, clock=lambda: NOW):
    registry = [
        {'device_id': 'firebeetle-ag', 'token_hash': hashlib.sha256(b'display').hexdigest(), 'scopes': ['display']},
        {'device_id': 'phone', 'token_hash': hashlib.sha256(b'phone').hexdigest(), 'scopes': ['phone']},
    ]
    statuses = LocalDeliveryStore(tmp_path / 'control')
    app = create_app(store=LocalDeliveryStore(tmp_path / 'delivery'), device_registry=registry,
                     status_store=statuses, now=clock)
    return TestClient(app), statuses


def test_board_posts_its_status_and_only_the_latest_is_kept(tmp_path):
    client, statuses = delivery(tmp_path)
    path = '/v1/display/device-status'
    assert client.post(path, json=report()).status_code == 401
    assert client.post(path, json=report(), headers={'Authorization': 'Bearer phone'}).status_code == 403
    headers = {'Authorization': 'Bearer display'}
    response = client.post(path, json=report(), headers=headers)
    assert response.status_code == 202
    assert response.json() == {'ok': True, 'received_at': '2026-11-03T21:40:00Z'}
    stored = statuses.read_json('device-status/firebeetle-ag.json')
    assert stored['device_id'] == 'firebeetle-ag' and stored['received_at'] == '2026-11-03T21:40:00Z'
    assert stored['report']['battery_mv'] == 3941
    assert 'units' not in stored['report']
    assert stored['report']['readable'][1]['label'] == 'Battery'
    assert client.post(path, json=report(battery_mv=3930), headers=headers).status_code == 202
    assert statuses.read_json('device-status/firebeetle-ag.json')['report']['battery_mv'] == 3930
    assert [name for name, _ in statuses.list_json('device-status')] == ['device-status/firebeetle-ag.json']


@pytest.mark.parametrize('change', [
    {'readable': []},
    {'readable': [{'section': 'Status', 'label': 'Device', 'value': 'x', 'state': 'bogus'}]},
    {'readable': [{'section': 'Status', 'label': 'Device', 'value': 'line\nbreak'}]},
    {'readable': [{'section': 'Status', 'label': 'Device', 'value': 'x' * 301}]},
    {'firmware': None},
    {'battery_samples': [{'at': '2026-11-03T12:00:00Z', 'mv': 9000}]},
    {'battery_samples': [{'at': '', 'mv': True}]},
])
def test_malformed_reports_are_refused(tmp_path, change):
    client, statuses = delivery(tmp_path)
    response = client.post('/v1/display/device-status', json=report(**change),
                           headers={'Authorization': 'Bearer display'})
    assert response.status_code == 400
    assert latest_record(statuses) is None


def test_report_without_storage_is_unavailable(tmp_path):
    registry = [{'device_id': 'b', 'token_hash': hashlib.sha256(b'display').hexdigest(), 'scopes': ['display']}]
    client = TestClient(create_app(store=LocalDeliveryStore(tmp_path), device_registry=registry))
    response = client.post('/v1/display/device-status', json=report(),
                           headers={'Authorization': 'Bearer display'})
    assert response.status_code == 503


def test_sanitizer_keeps_scalars_and_samples_and_drops_nested_objects():
    clean = sanitize_report(report(extra={'a': 1}, list_field=[1, 2], **{'Bad-Key': 1}))
    assert clean['battery_samples'][-1] == {'at': '2026-11-03T21:39:00Z', 'mv': 3941}
    assert {'units', 'extra', 'list_field', 'Bad-Key'}.isdisjoint(clean)
    assert clean['readable'][2]['state'] == 'neutral'


def test_board_now_follows_sleep_wake_and_overdue_reports():
    received = NOW
    asleep = board_now(report(), received, NOW + timedelta(minutes=5))
    assert asleep[0:2] == ('neutral', 'Asleep') and '05:52' in asleep[2]
    awake = board_now(report(awake_remaining_ms=9 * 60000), received, NOW + timedelta(minutes=2))
    assert awake[0:2] == ('ok', 'Awake') and '22:49' in awake[2]
    later = board_now(report(awake_remaining_ms=9 * 60000), received, NOW + timedelta(minutes=12))
    assert later[1] == 'Asleep'
    overdue = board_now(report(), received, datetime(2026, 11, 4, 5, 10, tzinfo=UTC))
    assert overdue[0] == 'warn' and 'tomorrow' not in overdue[2]
    assert board_now(report(power_mode='manual'), received, NOW)[1] == 'Manual mode'


def test_page_shows_battery_board_state_and_escaped_rows():
    value = sanitize_report(report(readable=[
        {'section': 'Status', 'label': 'Wi-Fi', 'value': 'Connected · <script>x</script>', 'state': 'ok'},
        {'section': 'Technical details', 'label': 'Firmware', 'value': 'ag-cloud-0.7.0'},
    ]))
    page = render_page({'received_at': '2026-11-03T21:40:00Z', 'report': value}, NOW + timedelta(minutes=3))
    assert '3.94 V' in page and 'About 61% · falling' in page
    assert '<svg class=spark' in page and '3 readings' in page
    assert 'Asleep' in page and 'Reported 22:40 · 3 minutes ago' in page
    assert 'Status at 22:40' in page
    assert '&lt;script&gt;' in page and '<script>' not in page
    assert '<details><summary>Technical details</summary>' in page


def test_page_without_battery_or_report():
    value = sanitize_report(report(battery_present=False, battery_samples=[]))
    page = render_page({'received_at': '2026-11-03T21:40:00Z', 'report': value}, NOW)
    assert 'Not detected' in page and '<svg' not in page
    assert 'No report from the board yet' in render_page(None, NOW)


def test_browser_serves_the_latest_report_to_signed_in_viewers(tmp_path):
    client, statuses = delivery(tmp_path, clock=lambda: datetime.now(UTC))
    assert client.post('/v1/display/device-status', json=report(),
                       headers={'Authorization': 'Bearer display'}).status_code == 202

    def verify(token, audience):
        if token != 'viewer':
            raise ValueError()
        return {'email': 'viewer@example.com'}

    browser = TestClient(browser_app(store=LocalDeliveryStore(tmp_path / 'display'),
                                     controller=RefreshController(MemoryStore(), Dispatcher()),
                                     verifier=verify, audience='configured', viewers=['viewer@example.com'],
                                     owners=[], origin='https://testserver', status_store=statuses),
                         base_url='https://testserver')
    assert browser.get('/board-status').status_code == 401
    page = browser.get('/board-status', headers={'X-Goog-IAP-JWT-Assertion': 'viewer'})
    assert page.status_code == 200
    assert '3.94 V' in page.text and 'Sleeps between updates' in page.text
    assert page.headers['cache-control'] == 'private, no-store'
    assert "script-src" in page.headers['content-security-policy']
