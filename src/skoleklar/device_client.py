"""One-shot authenticated refresh/status/image client. Never schedules or polls.

The private config holds the device's `token`. The gateway origin comes from
`--base-url` or the config's `base_url`; the gateway key from the config's
`gateway_key` or, without one, the Keychain, where deploy-gateway.sh stores it.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse
import httpx


def connection(base_url, config):
    """The gateway origin and request headers; ValueError names what is missing."""
    base = (base_url or config.get('base_url') or '').rstrip('/')
    if not base:
        raise ValueError('Give --base-url https://NAME.gateway.dev, or base_url in the config')
    url = urlparse(base)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('base_url must be HTTPS without credentials, query or fragment')
    gateway_key = config.get('gateway_key')
    if not gateway_key:
        from skoleklar.device_token import stored_gateway_api_key
        gateway_key = stored_gateway_api_key()
    if not gateway_key:
        raise ValueError('No gateway key in the config or the Keychain; deploy the gateway first')
    if not config.get('token'):
        raise ValueError('The config has no device token')
    return base, {'Authorization': 'Bearer ' + config['token'], 'x-api-key': gateway_key}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True,help='Private JSON with the device token (and optionally base_url, gateway_key)')
    parser.add_argument('--base-url',default=None,help='The gateway origin, https://NAME.gateway.dev (default: base_url in the config)')
    commands=parser.add_subparsers(dest='command',required=True)
    refresh=commands.add_parser('refresh')
    refresh.add_argument('--request-id',default=None,help='Reuse this UUID when retrying an uncertain request')
    status=commands.add_parser('status')
    status.add_argument('request_id')
    image=commands.add_parser('image')
    image.add_argument('--output',type=Path,required=True,help='Overwrite only the current image; do not archive')
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    try:
        base,headers=connection(args.base_url,config)
    except ValueError as error:
        parser.error(str(error))
    try:
        with httpx.Client(base_url=base,headers=headers,timeout=30) as client:
            if args.command=='refresh':
                from skoleklar.refresh_control import request_id
                identifier=request_id(args.request_id or str(uuid4()))
                # Print before network I/O so a lost response can be retried safely.
                print(json.dumps({'request_id':identifier}),flush=True)
                response=client.post('/v1/refresh',json={'request_id':identifier})
            elif args.command=='status':
                from skoleklar.refresh_control import request_id
                response=client.get('/v1/refresh/'+request_id(args.request_id))
            else:
                metadata=client.get('/v1/display/current')
                metadata.raise_for_status()
                meta=metadata.json()
                response=client.get('/v1/display/image',params={'version':meta['version']})
                response.raise_for_status()
                if hashlib.sha256(response.content).hexdigest()!=meta['sha256']:
                    raise ValueError('Image hash mismatch')
                import io
                from PIL import Image
                from skoleklar.dashboard_renderer import validate_panel_image
                with Image.open(io.BytesIO(response.content)) as png:
                    validate_panel_image(png)
                args.output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,'wb') as f:
                    os.fchmod(f.fileno(),0o600)
                    f.write(response.content)
                print(json.dumps({'version':meta['version'],'sha256':meta['sha256'],'output':str(args.output)}))
                return
            # Only the bounded API status contract is printed, never an arbitrary
            # proxy/provider error body or the authentication headers.
            payload=response.json()
            allowed={k:payload[k] for k in ('request_id','status','requested_at','duplicate','error') if k in payload}
            print(json.dumps({'http_status':response.status_code,**allowed}))
            if response.is_error:
                raise SystemExit(1)
    except (httpx.HTTPError,ValueError,KeyError):
        print(json.dumps({'error':'request_failed','instruction':'For a refresh retry, reuse the printed request_id.'}))
        raise SystemExit(1) from None

if __name__=='__main__':
    main()
