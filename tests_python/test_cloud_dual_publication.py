import json
import logging
from types import SimpleNamespace

import pytest

from tests_python.synthetic_household import PROFILE

from skoleklar import cloud_job
from skoleklar.runtime import gcp
from skoleklar.runtime import scan as scan_service
from skoleklar.dashboard_composition import CompositionFailed

FIXED = 'weekend activities must use the fixed weekend area'


@pytest.mark.parametrize('failure', [
    ValueError('private failure detail'),
    CompositionFailed('Dashboard composition failed validation: ValueError: private failure detail', [FIXED] * 6),
])
def test_archive_before_consolidation_and_dashboard_failure_preserves_phone(monkeypatch,tmp_path,caplog,failure):
    from google.cloud import storage
    from skoleklar import dashboard_composition, dashboard_context
    stages=[]
    written={}
    class Blob:
        def upload_from_string(self,*a,**k): pass
    class Bucket:
        def blob(self,name): return Blob()
        def list_blobs(self,prefix): return []
    class Store:
        def __init__(self,*a,**k): self.connection=SimpleNamespace(execute=lambda *a:None)
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def message_coverage(self,**k): return {}
    class Delivery:
        def __init__(self,bucket): self.bucket=bucket
        def write_json(self,name,value): written[(self.bucket,name)]=value
    monkeypatch.setenv('AG_PRIVATE_BUCKET','private')
    monkeypatch.setenv('AG_DELIVERY_BUCKET','delivery')
    monkeypatch.setenv('AG_DISPLAY_BUCKET','display')
    monkeypatch.setenv('AG_CONTROL_BUCKET','control')
    monkeypatch.setenv('AG_SCHOOL_MESSAGE_MODE','events-only')
    monkeypatch.setenv('AG_SCHOOL_MESSAGE_PROCESSOR','zdr')
    monkeypatch.setattr(storage,'Client',lambda:SimpleNamespace(bucket=lambda _:Bucket()))
    monkeypatch.setattr(scan_service,'SnapshotStore',Store)
    monkeypatch.setattr(gcp,'GCSDeliveryStore',Delivery)
    monkeypatch.setattr(gcp,'download_state',lambda *a,**k:{})
    monkeypatch.setattr(scan_service,'load_site_profile',lambda *a:PROFILE)
    monkeypatch.setattr(scan_service,'household_slots',lambda *a:{})
    monkeypatch.setattr(scan_service,'_load_event_confirmations',lambda *a:None)
    monkeypatch.setattr(gcp,'upload_state',lambda *a,**k:stages.append('checkpoint'))
    monkeypatch.setattr(scan_service,'_check_invariants',lambda *a:stages.append('invariants'))
    monkeypatch.setattr(scan_service,'_prepare_sqlite',lambda *a:None)
    monkeypatch.setattr(scan_service,'_run_quietly',lambda name,callback:stages.append(name))
    monkeypatch.setattr(scan_service,'delivery_items_from_snapshot',lambda *a,**k:[])
    monkeypatch.setattr(scan_service,'apply_verdicts',lambda store,items:stages.append('verdicts') or items)
    monkeypatch.setattr(scan_service,'build_delivery_manifest',lambda *a,**k:{'manifest_id':'phone-version'})
    monkeypatch.setattr(scan_service,'publish_manifest',lambda store,manifest:written.update(phone=manifest))
    monkeypatch.setattr(dashboard_context,'export_context',lambda *a,**k:{})
    def fail(*a,**k): raise failure
    monkeypatch.setattr(dashboard_composition,'compose',fail)
    from skoleklar import settings as credentials
    monkeypatch.setattr(credentials,'openrouter_api_key',lambda:'test')
    with caplog.at_level(logging.INFO, logger='agskoleintra.cloud_job'):
        result=cloud_job._run_cloud_sync()
    assert stages[:5]==['harvest','derive','archive_plan','archive','consolidate']
    # The invariant checks gate the upload.
    assert stages.index('invariants') < stages.index('checkpoint')
    assert result['status']=='partial'
    assert result['outputs']['iphone']['status']=='published'
    assert result['outputs']['dashboard']['status']=='failed'
    assert result['outputs']['dashboard']['failure_stage']=='compose'
    assert written['phone']['manifest_id']=='phone-version'
    assert 'verdicts' in stages
    assert 'private failure detail' not in str(written)
    event = next(json.loads(r.message) for r in caplog.records if '"dashboard_failed"' in r.message)
    # Each attempt's fixed validator reason is logged; the failure's own text never is.
    assert event.get('attempts') == ([FIXED] * 6 if isinstance(failure, CompositionFailed) else None)
    assert 'private failure detail' not in caplog.text
