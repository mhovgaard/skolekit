import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from skoleklar.delivery_api import create_app
from skoleklar.delivery_store import LocalDeliveryStore
from skoleklar.refresh_control import Conflict, RefreshBusy, RefreshController, RefreshUnavailable


class MemoryStore:
    def __init__(self):
        self.data = {}
        self.lock = Lock()
        self.generation = 0

    def read(self, name):
        with self.lock:
            value, generation = self.data.get(name, (None, 0))
            return dict(value) if value else None, generation

    def put(self, name, value, generation):
        with self.lock:
            if self.data.get(name, (None, 0))[1] != generation:
                raise Conflict()
            self.generation += 1
            self.data[name] = (dict(value), self.generation)
            return self.generation

    def delete(self, name, generation):
        with self.lock:
            if self.data.get(name, (None, 0))[1] == generation:
                del self.data[name]


class Dispatcher:
    def __init__(self):
        self.calls = 0
        self.state = "running"
        self.fail = False

    def start(self):
        self.calls += 1
        if self.fail:
            raise TimeoutError("private provider response")
        return "operation"

    def status(self, _):
        return self.state


def test_retries_and_other_devices_never_start_a_duplicate():
    store, dispatcher = MemoryStore(), Dispatcher()
    control = RefreshController(store, dispatcher)
    identifier = str(uuid4())
    assert control.trigger("a", identifier)[1] is False
    assert RefreshController(store, dispatcher).trigger("a", identifier)[1] is True
    with pytest.raises(RefreshBusy):
        control.trigger("b", str(uuid4()))
    assert control.status("b", identifier) is None
    dispatcher.state = "complete"
    control.trigger("b", str(uuid4()))
    assert dispatcher.calls == 2


def test_concurrent_requests_dispatch_once():
    dispatcher = Dispatcher()
    controller = RefreshController(MemoryStore(), dispatcher)
    def run(_):
        try:
            controller.trigger("a", str(uuid4()))
        except RefreshBusy:
            pass
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(12)))
    assert dispatcher.calls == 1


def test_uncertain_dispatch_is_not_retried_and_keeps_lock():
    dispatcher = Dispatcher()
    dispatcher.fail = True
    controller = RefreshController(MemoryStore(), dispatcher)
    identifier = str(uuid4())
    with pytest.raises(RefreshUnavailable):
        controller.trigger("a", identifier)
    assert controller.trigger("a", identifier)[0]["status"] == "dispatch_uncertain"
    with pytest.raises(RefreshBusy):
        controller.trigger("a", str(uuid4()))
    assert dispatcher.calls == 1


def test_api_requires_explicit_refresh_scope_and_valid_request(tmp_path):
    registry = [{"device_id": name, "token_hash": hashlib.sha256(name.encode()).hexdigest(), "scopes": scopes}
                for name, scopes in [("reader", ["display"]), ("remote", ["refresh"]), ("other", ["refresh"])]]
    dispatcher = Dispatcher()
    app = create_app(store=LocalDeliveryStore(tmp_path), token_hash=hashlib.sha256(b"phone").hexdigest(),
                     device_registry=registry, refresh_controller=RefreshController(MemoryStore(), dispatcher))
    client = TestClient(app)
    payload = {"request_id": str(uuid4())}
    assert client.post('/v1/refresh', json=payload).status_code == 401
    for token in ["phone", "reader"]:
        assert client.post('/v1/refresh', json=payload, headers={"Authorization": f"Bearer {token}"}).status_code == 403
    headers = {"Authorization": "Bearer remote"}
    assert client.post('/v1/refresh', json={**payload, "job": "attacker"}, headers=headers).status_code == 400
    assert client.post('/v1/refresh', json=payload, headers=headers).status_code == 202
    assert client.post('/v1/refresh', json=payload, headers=headers).status_code == 200
    assert client.get('/v1/refresh/' + payload['request_id'], headers={"Authorization": "Bearer other"}).status_code == 404
    assert dispatcher.calls == 1
