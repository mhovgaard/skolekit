#!/usr/bin/env python3
"""Provision scoped device credentials and the message-handle key.

The database is seeded by seed-private-state.sh alone; new secrets are
replicated in the household's region only.

The cloud must use the same message-handle key as the local database it is
seeded from. A household that scanned with messages on already has that key in
the Keychain. A household that never processed messages has none, and its
database holds no message handles, so a new key is generated and kept in the
Keychain before it is uploaded. A database that holds handles without a local
key is refused: a different key would break every stored handle.

Run from the repository using .venv/bin/python. Secret values never go to stdout.
Existing databases and differing secret values are never silently overwritten.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess

KEYCHAIN_SERVICE = 'ag-skoleintra'
HANDLE_KEY_ACCOUNT = 'message-handle-key'
DATABASE_FILENAME = 'school-restricted.db'
# Every table that stores an opaque message handle (migrations/0001_baseline.sql).
MESSAGE_TABLES = ('message_thread_state', 'message_event_facts', 'message_processing_receipts',
                  'message_processing_attempts', 'message_event_feedback')


def household_database() -> Path:
    """The database seed-private-state.sh uploads."""
    home = os.environ.get('AG_SCHOOL_HOME') or str(Path.home() / '.config' / 'ag-skoleintra')
    return Path(home).expanduser() / DATABASE_FILENAME


def holds_message_handles(database: Path) -> bool:
    """Whether the database stores any handle made with a message-handle key."""
    if not database.exists():
        return False
    with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in MESSAGE_TABLES:
            if table in tables and connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                return True
        if 'item_workflow' in tables and connection.execute(
                "SELECT 1 FROM item_workflow WHERE stable_key LIKE 'message-event:%' LIMIT 1").fetchone():
            return True
    return False


def local_handle_key(keyring, database: Path) -> str:
    """The Keychain's handle key, or a new one when no stored handle depends on a key."""
    key = keyring.get_password(KEYCHAIN_SERVICE, HANDLE_KEY_ACCOUNT)
    if key:
        return key
    if holds_message_handles(database):
        raise ValueError('The local database holds message handles but the Keychain has no message-handle key; '
                         'refusing to generate a different key')
    key = secrets.token_urlsafe(32)
    keyring.set_password(KEYCHAIN_SERVICE, HANDLE_KEY_ACCOUNT, key)
    print('No message handles are stored yet; a new message-handle key was saved in the Keychain.')
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--region', default='europe-west1', help='Where new secrets are replicated')
    parser.add_argument('--seed-restricted', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    project = args.project
    if args.seed_restricted:
        raise SystemExit('Seed the database with seed-private-state.sh; this script no longer uploads it.')
    def gc(*parts, payload=None, missing_ok=False):
        result = subprocess.run(['gcloud', *parts, '--project='+project, '--quiet'],
                                input=payload, capture_output=True)
        if result.returncode and not missing_ok:
            raise RuntimeError('gcloud operation failed: '+parts[0]+' '+parts[1])
        return result
    active = subprocess.run(['gcloud','config','get-value','project'],capture_output=True,text=True,check=True).stdout.strip()
    if active != project:
        raise ValueError('Project is not the active gcloud project')

    def secret(name, value, identity):
        exists = gc('secrets','describe',name,missing_ok=True).returncode == 0
        if not exists:
            gc('secrets','create',name,'--replication-policy=user-managed','--locations='+args.region)
        current = gc('secrets','versions','access','latest','--secret='+name,missing_ok=True)
        if current.returncode:
            gc('secrets','versions','add',name,'--data-file=-',payload=value)
        elif current.stdout != value:
            raise ValueError('Existing secret differs; explicit reconciliation required: '+name)
        gc('secrets','add-iam-policy-binding',name,
           '--member=serviceAccount:'+identity+'@'+project+'.iam.gserviceaccount.com',
           '--role=roles/secretmanager.secretAccessor')

    root = Path('.ag-school/devices')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path = root/'local-controller.json'
    if not path.exists():
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output:
            json.dump({'device_id':'local-controller','token':secrets.token_urlsafe(48)},output)
    device=json.loads(path.read_text())
    registry=[{'device_id':device['device_id'],'token_hash':hashlib.sha256(device['token'].encode()).hexdigest(),'scopes':['refresh','display']}]
    secret('agskoleintra-device-registry',json.dumps(registry).encode(),'ag-delivery')
    import keyring
    key=local_handle_key(keyring, household_database())
    secret('agskoleintra-message-handle-key',key.encode(),'ag-sync')
    print('Dashboard bootstrap completed; secret values were not emitted.')

if __name__ == '__main__':
    main()
