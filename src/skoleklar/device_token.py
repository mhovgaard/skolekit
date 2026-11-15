from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess

from skoleklar.settings import KEYCHAIN_SERVICE

KEYCHAIN_ACCOUNT = "family-primary-iphone-device-token"
GATEWAY_KEYCHAIN_ACCOUNT = "family-primary-iphone-gateway-api-key"


def stored_device_token() -> str | None:
    import keyring

    return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def ensure_device_token(*, rotate: bool = False) -> str:
    import keyring

    token = stored_device_token()
    if token and not rotate:
        return token
    token = secrets.token_urlsafe(48)
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, token)
    return token


def token_hash(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def stored_gateway_api_key() -> str | None:
    import keyring

    return keyring.get_password(KEYCHAIN_SERVICE, GATEWAY_KEYCHAIN_ACCOUNT)


def store_gateway_api_key(value: str) -> None:
    import keyring

    normalized = value.strip()
    if len(normalized) < 20 or len(normalized) > 512:
        raise ValueError("Gateway API key has an unexpected length")
    keyring.set_password(KEYCHAIN_SERVICE, GATEWAY_KEYCHAIN_ACCOUNT, normalized)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the iPhone delivery token")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--rotate", action="store_true")
    subparsers.add_parser("status")
    subparsers.add_parser("copy")
    subparsers.add_parser("gateway-status")
    subparsers.add_parser("gateway-copy")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command in {"gateway-status", "gateway-copy"}:
        gateway_key = stored_gateway_api_key()
        if args.command == "gateway-status":
            print(json.dumps({"configured": gateway_key is not None}))
            raise SystemExit(0 if gateway_key else 2)
        if gateway_key is None:
            print("No gateway API key is stored. Deploy the API Gateway first.")
            raise SystemExit(2)
        subprocess.run(["pbcopy"], input=gateway_key.encode("utf-8"), check=True)
        print("Gateway API key copied to the clipboard. Clear the clipboard after use.")
        return
    if args.command == "create":
        token = ensure_device_token(rotate=args.rotate)
        print(token_hash(token))
        return
    token = stored_device_token()
    if args.command == "status":
        print(json.dumps({"configured": token is not None}))
        raise SystemExit(0 if token else 2)
    if token is None:
        print("No device token is stored. Run the create command first.")
        raise SystemExit(2)
    subprocess.run(["pbcopy"], input=token.encode("utf-8"), check=True)
    print("Device token copied to the clipboard. Clear the clipboard after use.")


if __name__ == "__main__":
    main()
