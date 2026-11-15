import copy
import hashlib
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skoleklar.browser_api import create_app as browser_app
from skoleklar.delivery_api import create_app
from skoleklar.delivery_store import LocalDeliveryStore
from skoleklar.display_store import make_bundle
from skoleklar.dashboard_composition import compose
from tests_python.test_dashboard_rendering import state, weekday_grid_state
from tests_python.test_refresh_control import Dispatcher, MemoryStore, RefreshController


def fixture(tmp_path):
    now = datetime.now(UTC)
    data = state()
    data['freshness'] = {'source_updated_at': now.isoformat(), 'composed_at': now.isoformat(),
                         'expires_at': (now + timedelta(hours=36)).isoformat(), 'status': 'fresh'}
    store = LocalDeliveryStore(tmp_path)
    bundle = make_bundle(data, 'test-version', now=now)
    store.write_json('current.json', bundle)
    return data, store, bundle


def test_device_access_expiry_hash_and_version(tmp_path):
    _, store, bundle = fixture(tmp_path)
    registry = [{'device_id':'pi','token_hash':hashlib.sha256(b'pi').hexdigest(),'scopes':['display']}]
    app = create_app(store=LocalDeliveryStore(tmp_path/'receipts'), display_store=store, device_registry=registry)
    client = TestClient(app)
    headers={'Authorization':'Bearer pi'}
    assert client.get('/v1/display/image').status_code == 401
    image=client.get('/v1/display/image',headers=headers)
    assert image.status_code == 200
    assert hashlib.sha256(image.content).hexdigest() == bundle['metadata']['sha256']
    assert bundle['metadata']['palette_colours'] == 6
    assert bundle['metadata']['pixel_format'] == 'spectra6-indexed'
    assert bundle['metadata']['panel_codes'] == {
        'black': 0, 'white': 1, 'yellow': 2, 'red': 3, 'blue': 5, 'green': 6,
    }
    assert bundle['metadata']['frame_format'] == 'spectra6-4bpp-high-nibble-first'
    assert bundle['metadata']['frame_bytes'] == 192_000
    frame = client.get('/v1/display/frame', headers=headers)
    assert frame.status_code == 200
    assert len(frame.content) == 192_000
    assert hashlib.sha256(frame.content).hexdigest() == bundle['metadata']['frame_sha256']
    assert frame.headers['x-frame-format'] == bundle['metadata']['frame_format']
    assert client.get('/v1/display/frame', headers={**headers, 'If-None-Match': frame.headers['etag']}).status_code == 304
    assert client.get('/v1/display/frame?version=old', headers=headers).status_code == 409
    assert client.get('/v1/display/image?version=old',headers=headers).status_code == 409
    assert client.get('/v1/display/image',headers={**headers,'If-None-Match':image.headers['etag']}).status_code == 304
    bundle['metadata']['expires_at']=(datetime.now(UTC)-timedelta(seconds=1)).isoformat()
    store.write_json('current.json',bundle)
    assert client.get('/v1/display/image',headers=headers).status_code == 503


def test_display_connection_status_separates_auth_from_publication(tmp_path):
    _, store, bundle = fixture(tmp_path)
    registry = [
        {'device_id': 'firebeetle-ag', 'token_hash': hashlib.sha256(b'display').hexdigest(), 'scopes': ['display']},
        {'device_id': 'phone', 'token_hash': hashlib.sha256(b'phone').hexdigest(), 'scopes': ['phone']},
    ]
    dispatcher = Dispatcher()
    controller = RefreshController(MemoryStore(), dispatcher)
    client = TestClient(create_app(store=LocalDeliveryStore(tmp_path / 'receipts'),
                                  display_store=store, device_registry=registry,
                                  refresh_controller=controller))
    path = '/v1/display/status'
    assert client.get(path).status_code == 401
    assert client.get(path, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert client.get(path, headers={'Authorization': 'Bearer phone'}).status_code == 403
    headers = {'Authorization': 'Bearer display'}
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data['ok'] is True and data['device_id'] == 'firebeetle-ag'
    assert data['publication_available'] is True
    assert data['metadata']['frame_bytes'] == 192_000
    assert set(data['metadata']) == {'version', 'generated_at', 'source_updated_at', 'expires_at',
                                     'status', 'width', 'height', 'frame_bytes', 'frame_format', 'frame_sha256'}
    assert response.headers['cache-control'] == 'private, no-store'
    assert client.post('/v1/refresh', headers=headers, json={}).status_code == 403
    bundle['metadata']['expires_at'] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.write_json('current.json', bundle)
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert response.json()['publication_available'] is False
    assert response.json()['metadata'] is None
    assert client.get('/v1/display/current', headers=headers).status_code == 503
    assert dispatcher.calls == 0


def test_display_status_without_storage_remains_authenticated(tmp_path):
    registry = [{'device_id': 'device', 'token_hash': hashlib.sha256(b'display').hexdigest(), 'scopes': ['display']}]
    client = TestClient(create_app(store=LocalDeliveryStore(tmp_path), device_registry=registry))
    response = client.get('/v1/display/status', headers={'Authorization': 'Bearer display'})
    assert response.status_code == 200
    assert response.json()['publication_available'] is False
    assert response.json()['metadata'] is None


def test_browser_verified_identity_owner_csrf_and_no_get_dispatch(tmp_path):
    _, store, _ = fixture(tmp_path)
    dispatcher=Dispatcher()
    def verify(token,audience):
        if token not in {'owner','viewer','outsider'} or audience != 'configured':
            raise ValueError()
        return {'email':token+'@example.com'}
    app=browser_app(store=store, controller=RefreshController(MemoryStore(),dispatcher), verifier=verify,
                    audience='configured',viewers=['viewer@example.com'],owners=['owner@example.com'],origin='https://testserver')
    client=TestClient(app,base_url='https://testserver')
    assert client.get('/').status_code == 401
    assert client.get('/',headers={'X-Goog-Authenticated-User-Email':'owner@example.com'}).status_code == 401
    assert client.get('/',headers={'X-Goog-IAP-JWT-Assertion':'outsider'}).status_code == 401
    owner={'X-Goog-IAP-JWT-Assertion':'owner'}
    viewer={'X-Goog-IAP-JWT-Assertion':'viewer'}
    assert client.get('/',headers=viewer).status_code == 200
    device_png = client.get('/preview.png',headers=owner)
    simulated_png = client.get('/preview.png?preview=1',headers=owner)
    assert device_png.status_code == simulated_png.status_code == 200
    from PIL import Image
    with Image.open(io.BytesIO(device_png.content)) as image:
        assert image.mode == 'P'
    with Image.open(io.BytesIO(simulated_png.content)) as image:
        assert image.mode == 'RGB'
        assert set(image.getdata()) <= {
            (0x19, 0x1E, 0x21), (0xE8, 0xE8, 0xE8), (0xEF, 0xDE, 0x44),
            (0xB2, 0x13, 0x18), (0x21, 0x57, 0xBA), (0x12, 0x5F, 0x20),
        }
    preview_page = client.get('/panel-preview', headers=owner, follow_redirects=False)
    assert preview_page.status_code == 303 and preview_page.headers['location'] == '/board-status'
    page=client.get('/control',headers=owner)
    assert "href='/board-status'>Tavlestatus</a>" in page.text
    assert 'Panelsimulering' not in page.text
    assert dispatcher.calls == 0
    assert client.post('/admin/refresh',headers=viewer).status_code == 403
    assert client.post('/admin/refresh',headers=owner).status_code == 403
    import re
    csrf=re.search("name='csrf' value='([^']+)'",page.text)[1]
    identifier=re.search("name='request_id' value='([^']+)'",page.text)[1]
    response = client.post('/admin/refresh',headers={**owner,'Origin':'https://testserver'},data={'csrf':csrf,'request_id':identifier},follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/'
    assert dispatcher.calls == 1
    dispatcher.state = 'complete'
    page=client.get('/control',headers=owner)
    csrf=re.search("name='csrf' value='([^']+)'",page.text)[1]
    identifier=re.search("name='request_id' value='([^']+)'",page.text)[1]
    response = client.post('/admin/refresh',headers={**owner,'Sec-Fetch-Site':'same-origin'},data={'csrf':csrf,'request_id':identifier},follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/'
    assert dispatcher.calls == 2


def _v2_context(now, items=(), messages=None):
    data = weekday_grid_state(now)
    return {'now': now, 'items': list(items), 'children': ['ChildA', 'ChildB'],
            'freshness': data['freshness'],
            'messages': messages or {'checked': True, 'bodies_retained': 0, 'unread_count': 7,
                                     'new_count': 0, 'items': []}}


def _slots(context, lines=None, omitted=()):
    """A v2 answer for the context's calendar; `lines` maps (date, child) to line dicts."""
    from skoleklar.dashboard_composition import school_calendar
    calendar = school_calendar(context)
    lines = lines or {}

    def child(day, name):
        return {'name': name, 'heading': '', 'lines': lines.get((day, name), [])}
    return {'hero': {'children': [child(calendar['hero']['date'], name) for name in context['children']]},
            'weekdays': [{'date': day['date'], 'children': [child(day['date'], name) for name in context['children']],
                          'shared': None, 'summary': ''} for day in calendar['weekdays']],
            'omitted': list(omitted), 'limitations': []}


def _fact(ref, child, day, kind, title, **values):
    return {'ref': ref, 'child': child, 'effective_date': day, 'end_date': None, 'type': kind,
            'title': title, 'summary': '', 'actionability': 'informational', 'urgency': 'routine',
            'confidence': 0.9, 'needs_review': False, 'private_message_fact': False, 'grouped': False,
            **values}


def test_composition_takes_inbox_and_freshness_from_the_context_not_the_model():
    context = _v2_context('2026-09-22T08:00:00+02:00')
    context['messages']['unread_count'] = 99

    class Client:
        calls = 0

        def generate(self, document, **kwargs):
            self.calls += 1
            return {'output': _slots(context)}

    provider = Client()
    state, audit = compose(context, provider)
    assert provider.calls == 1
    assert state['messages']['unread_count'] == 99 and state['freshness'] == context['freshness']
    assert state['hero']['day'] == 'Tirsdag 22. sep.' and state['hero']['state'] == 'I dag'


def test_composition_retry_names_the_rule_and_its_facts_then_falls_back_to_the_caller():
    now = '2026-09-22T08:00:00+02:00'
    event = _fact('fact-1', 'ChildA', '2026-09-23', 'event', 'Klassearrangement',
                  private_message_fact=True, message_thread=1)
    bring = _fact('fact-2', 'ChildA', '2026-09-23', 'bring', 'Medbring madpakke',
                  private_message_fact=True, message_thread=1, actionability='required')
    context = _v2_context(now, [event, bring])
    shown = {('2026-09-23', 'ChildA'): [{'refs': ['fact-1'], 'priority': 'event', 'text': 'Klassearrangement'}]}
    # Calling the bring-item a duplicate of the event is accepted as a tag, but the
    # board would then hide a required item, which the validator refuses.
    hiding = _slots(context, shown, [{'ref': 'fact-2', 'reason': 'duplicate', 'same_as': 'fact-1'}])
    fixed = _slots(context, {('2026-09-23', 'ChildA'): [
        {'refs': ['fact-1', 'fact-2'], 'priority': 'obligation', 'text': 'Klassearrangement; tag madpakke med'}]})

    class Client:
        def __init__(self, answers):
            self.answers, self.documents = list(answers), []

        def generate(self, document, **kwargs):
            self.documents.append(copy.deepcopy(document))
            return {'output': copy.deepcopy(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])}

    provider = Client([hiding, fixed])
    state, _ = compose(context, provider)
    assert len(provider.documents) == 2
    feedback = provider.documents[1]['validation_feedback']
    assert 'private event row must include its retained bring-item [fact-2; fact-1 at' in feedback
    assert 'add its ref to that event' in feedback
    assert state['weekdays'][2]['children'][0]['lines'] == ['Besked: Klassearrangement; tag madpakke med']

    stubborn = Client([hiding])
    with pytest.raises(ValueError) as failure:
        compose(context, stubborn)
    assert len(stubborn.documents) == 3
    assert failure.value.attempts == [
        'private event row must include its retained bring-item '
        '[fact-2; fact-1 at weekdays[2].children[0].lines[0]]'] * 3


def test_weekend_composition_supplies_exact_upcoming_school_calendar():
    now = '2026-10-24T13:00:00+02:00'
    expected = [
        {'date': '2026-10-26', 'day': 'Mandag 26. okt.'},
        {'date': '2026-10-27', 'day': 'Tirsdag 27. okt.'},
        {'date': '2026-10-28', 'day': 'Onsdag 28. okt.'},
        {'date': '2026-10-29', 'day': 'Torsdag 29. okt.'},
        {'date': '2026-10-30', 'day': 'Fredag 30. okt.'},
    ]
    context = _v2_context(now)

    class Client:
        def generate(self, document, **kwargs):
            assert document['calendar'] == {
                'mode': 'weekend', 'week_label': 'UGE 44',
                'hero': expected[0], 'weekdays': expected,
                'weekend': [{'date': '2026-10-24', 'day': 'Lørdag 24. okt.'},
                            {'date': '2026-10-25', 'day': 'Søndag 25. okt.'}],
            }
            answer = _slots(context)
            for day in answer['weekdays']:
                day['date'] = '2026-10-19'  # dates the model gets wrong are not used
            return {'output': answer}

    state, _ = compose(context, Client())
    assert state['week_label'] == 'UGE 44'
    assert state['hero']['day'] == 'Mandag 26. okt.' and state['hero']['state'] == 'Næste skoledag'
    assert [(day['date'], day['day']) for day in state['weekdays']] == [
        (item['date'], item['day']) for item in expected]


def test_composition_builds_message_rows_from_allowed_source_metadata():
    now = datetime.now(UTC)
    messages = {'checked': True, 'bodies_retained': 0, 'unread_count': 7, 'items': [
        {'participants': ['Teacher T.', 'Other O.'], 'child': 'ChildA',
         'activity': '2026-09-25T14:36', 'observed_at': now.isoformat(), 'unread': True,
         'new_thread': True, 'new_message': False, 'latest_unretrieved': True},
        {'participants': ['Guide G.'], 'child': 'ChildB', 'activity': '2026-09-24',
         'observed_at': now.isoformat(), 'unread': False, 'new_thread': False,
         'new_message': True, 'latest_unretrieved': True},
    ]}
    context = _v2_context('2026-09-22T08:00:00+02:00', messages=messages)

    class Client:
        def generate(self, *args, **kwargs):
            return {'output': _slots(context)}

    result, _ = compose(context, Client())
    assert result['messages']['items'] == [
        {'participant': 'Teacher T.', 'children': ['ChildA'],
         'activity': '25/09 14:36', 'unread': True, 'new_thread': True,
         'new_message': False, 'latest_unretrieved': True},
        {'participant': 'Guide G.', 'children': ['ChildB'], 'activity': '24/09',
         'unread': False, 'new_thread': False, 'new_message': True,
         'latest_unretrieved': True},
    ]
    assert result['hero']['children'][0] == {'name': 'ChildA', 'heading': 'Ingen punkter', 'lines': [],
                                             'kinds': []}


@pytest.mark.parametrize('attribute,value', [('versioning_enabled',True),('retention_period',60),('default_event_based_hold',True)])
def test_display_publication_refuses_retained_storage(attribute,value):
    from types import SimpleNamespace
    from skoleklar.display_store import GCSCurrentDisplayStore
    bucket=SimpleNamespace(reload=lambda:None, versioning_enabled=False,retention_period=None,
                           default_event_based_hold=False,soft_delete_policy=SimpleNamespace(retention_duration_seconds=0))
    setattr(bucket,attribute,value)
    store=GCSCurrentDisplayStore.__new__(GCSCurrentDisplayStore)
    store.bucket=bucket
    with pytest.raises(ValueError,match='history'):
        store.write_json('current.json',{})


def test_display_publication_refuses_subject_history_object():
    from skoleklar.display_store import GCSCurrentDisplayStore
    store=GCSCurrentDisplayStore.__new__(GCSCurrentDisplayStore)
    with pytest.raises(ValueError,match='current bundle'):
        store.write_json('history/old.json',{})


def test_served_dashboard_csp_allows_exactly_its_script_and_embedded_fonts(tmp_path):
    import base64
    import re

    _, store, _ = fixture(tmp_path)

    def verify(token, audience):
        return {'email': 'viewer@example.com'}

    app = browser_app(store=store, controller=RefreshController(MemoryStore(), Dispatcher()), verifier=verify,
                      audience='configured', viewers=['viewer@example.com'], owners=[], origin='https://testserver')
    response = TestClient(app, base_url='https://testserver').get('/', headers={'X-Goog-IAP-JWT-Assertion': 'v'})
    assert response.status_code == 200
    policy = response.headers['content-security-policy']
    scripts = re.findall(r'<script>(.*?)</script>', response.text, re.S)
    assert len(scripts) == 1
    digest = 'sha256-' + base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert f"script-src '{digest}'" in policy
    assert "font-src data:" in policy and "default-src 'none'" in policy
    assert "img-src 'self' data:" in policy
    assert 'unsafe-eval' not in policy and "script-src 'unsafe-inline'" not in policy
