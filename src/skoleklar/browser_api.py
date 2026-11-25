"""IAP-only browser view. No school credentials or device tokens in this service."""
from __future__ import annotations

import html
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from skoleklar import processing_cost, scan_log
from skoleklar.board_status import latest_record, render_page as render_board_status, status_store_from_environment
from skoleklar.dashboard_renderer import FOLD_SCRIPT_CSP_HASH
from skoleklar.display_store import (
    current_bundle,
    display_store_from_environment,
    png_bytes,
    simulated_png_bytes,
)
from skoleklar.refresh_control import controller_from_environment, request_id, RefreshBusy


REFRESH_COOKIE = "ag_refresh"
TRACKING_LIMIT = timedelta(minutes=35)
BROWSER_CSS = """
body.browser-page{display:flex;flex-direction:column;align-items:center;justify-content:flex-start;
 margin:0;padding:24px;gap:18px;min-height:100vh;background:#F6F7F8;color:#131A26;
 font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;box-sizing:border-box}
.browser-bar,.browser-empty{width:min(100%,800px);box-sizing:border-box}
.browser-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.browser-toolbar form{margin:0}
.browser-bar button{font-family:inherit;font-size:14px;font-weight:700;line-height:1.3;
 min-height:44px;padding:10px 18px;border:1px solid #2B4C8C;border-radius:6px;
 background:#2B4C8C;color:#fff;cursor:pointer}
.browser-bar button:disabled{cursor:default;background:#DCE2E8;border-color:#DCE2E8;color:#131A26}
.browser-bar a{color:#2B4C8C;text-underline-offset:3px}
.browser-bar a:focus-visible,.browser-bar button:focus-visible{outline:3px solid #C7A640;outline-offset:3px}
.browser-links{display:flex;gap:16px;flex-wrap:wrap;font-size:14px}
.refresh-status{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px;
 font-size:14px;line-height:1.5;overflow-wrap:anywhere}
.refresh-status p{margin:0}
.refresh-status progress{width:150px;max-width:100%;height:8px;accent-color:#2B4C8C}
.refresh-status[data-state=complete]{color:#2B4C8C}
.refresh-status[data-state=failed],.refresh-status[data-state=dispatch_uncertain],
.refresh-status[data-state=unavailable],.refresh-status[data-state=slow]{
 padding:10px 12px;background:#fff6d8;border-radius:6px}
.browser-empty{padding:24px;background:#EBEEF0;line-height:1.5}
.browser-empty h1{font-size:24px;margin-top:0}
@media(max-width:520px){body.browser-page{padding:16px;gap:14px}.browser-links{gap:12px}}
"""


def refresh_bar(*, owner, available, csrf, phase, auto_check):
    """A native HTML form and status indicator; only an active request reloads the page."""
    controls = ""
    messages = {
        "running": "Opdaterer skoleoverblikket. Det tager normalt et par minutter.",
        "complete": "Opdateringen er færdig.",
        "failed": "Opdateringen mislykkedes. Du kan prøve igen.",
        "dispatch_uncertain": "Starten kunne ikke bekræftes. Kontrollér status igen, før du starter på ny.",
        "unavailable": "Status kunne ikke hentes. Opdateringen kan stadig være i gang.",
        "slow": "Opdateringen tager længere tid end forventet. Kontrollér status igen.",
        "not_found": "Opdateringen kunne ikke findes. Du kan starte en ny.",
        "busy": "En opdatering er allerede i gang. Prøv at kontrollere igen om lidt.",
        "disabled": "Opdatering er midlertidigt utilgængelig.",
    }
    if owner:
        can_start = available and phase in {"idle", "complete", "failed", "not_found"}
        if can_start:
            controls = ("<form method='post' action='/admin/refresh'>"
                        f"<input type='hidden' name='csrf' value='{html.escape(csrf, quote=True)}'>"
                        f"<input type='hidden' name='request_id' value='{uuid4()}'>"
                        "<button type='submit'>Opdater nu</button></form>")
        else:
            label = "Opdaterer…" if phase == "running" else "Opdater nu"
            controls = f"<button type='button' disabled>{label}</button>"
    feedback = ""
    if owner and phase in messages:
        progress = ""
        if phase == "running":
            progress = "<progress aria-label='Opdatering i gang'></progress>"
        elif phase == "complete":
            progress = "<progress value='1' max='1' aria-label='Opdatering færdig'></progress>"
        manual = (" <a href='/'>Kontrollér status</a>" if phase in {
            "running", "dispatch_uncertain", "unavailable", "slow", "busy"} else "")
        automatic = " Siden følger automatisk med." if auto_check else ""
        feedback = (f"<div class='refresh-status' data-state='{phase}' role='status'>"
                    f"{progress}<p>{messages[phase]}{automatic}{manual}</p></div>")
    return ("<header class='browser-bar'><div class='browser-toolbar'>" + controls
            + "<nav class='browser-links' aria-label='Visning'>"
            "<a href='/preview.png'>E-ink-fil</a><a href='/board-status'>Tavlestatus</a>"
            "</nav></div>" + feedback + "</header>")


def verify_iap(assertion, audience):
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token
    claims = id_token.verify_token(assertion, GoogleRequest(), audience=audience,
                                  certs_url="https://www.gstatic.com/iap/verify/public_key")
    if claims.get("iss") != "https://cloud.google.com/iap":
        raise ValueError("Invalid issuer")
    return claims


def create_app(*, store=None, controller=None, verifier=verify_iap, audience=None,
               viewers=None, owners=None, origin=None, status_store=None, viewer=None):
    """The browser board. Behind IAP by default; `viewer(request)` replaces that check
    (the local runtime's loopback viewer, `skoleklar serve`) and returns the viewer or None."""
    displays = store or display_store_from_environment()
    statuses = status_store if status_store is not None else status_store_from_environment()
    refresh = controller or controller_from_environment()
    audience = audience or os.getenv("AG_IAP_AUDIENCE")
    allowed = {x.strip().lower() for x in (viewers if viewers is not None else os.getenv("AG_VIEWERS", "").split(",")) if x.strip()}
    admins = {x.strip().lower() for x in (owners if owners is not None else os.getenv("AG_OWNERS", "").split(",")) if x.strip()}
    if isinstance(origin, (set, frozenset, list, tuple)):
        origins = set(origin)
    elif origin is not None:
        origins = {origin}
    else:
        origins = {value for value in (
            os.getenv("AG_BROWSER_ORIGIN"),
            *os.getenv("AG_BROWSER_ORIGINS", "").split("|"),
        ) if value}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def identity(request):
        if viewer is not None:
            return viewer(request)
        try:
            if not audience:
                raise ValueError()
            claims = verifier(request.headers.get("x-goog-iap-jwt-assertion", ""), audience)
            email = claims["email"].lower()
            if email not in allowed | admins:
                raise ValueError()
            return email
        except Exception:
            return None

    @app.middleware("http")
    async def secure(request, call_next):
        user = await run_in_threadpool(identity, request)
        if user is None:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
        else:
            request.state.user = user
            response = await call_next(request)
        response.headers.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": f"default-src 'none'; script-src '{FOLD_SCRIPT_CSP_HASH}'; style-src 'unsafe-inline'; font-src data:; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'",
                                 "Referrer-Policy": "no-referrer"})
        return response

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        owner = request.state.user in admins
        phase, auto_check, clear_tracking = "idle", False, False
        identifier = request.cookies.get(REFRESH_COOKIE) if owner else None
        if identifier:
            try:
                request_id(identifier)
            except (ValueError, TypeError):
                identifier, clear_tracking = None, True
        if identifier:
            try:
                if refresh is None:
                    raise ValueError("Refresh unavailable")
                record = await run_in_threadpool(refresh.status, "browser:" + request.state.user, identifier)
                phase = record["status"] if record else "not_found"
                if phase == "running":
                    started = datetime.fromisoformat(record["requested_at"])
                    auto_check = timedelta(0) <= datetime.now(UTC) - started < TRACKING_LIMIT
                    if not auto_check:
                        phase = "slow"
                if phase in {"complete", "failed", "not_found"}:
                    clear_tracking = True
            except Exception:
                phase = "unavailable"
        if phase == "idle":
            if refresh is None:
                phase = "disabled"
            elif request.query_params.get("refresh_notice") == "busy":
                phase = "busy"
        csrf = request.cookies.get("ag_csrf", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", csrf):
            csrf = secrets.token_urlsafe(32)
        bar = refresh_bar(owner=owner, available=refresh is not None, csrf=csrf,
                          phase=phase, auto_check=auto_check)
        head = f"<style>{BROWSER_CSS}</style>"
        if auto_check:
            head += "<meta http-equiv='refresh' content='5;url=/'>"
        try:
            bundle = await run_in_threadpool(current_bundle, displays)
            page = bundle["html"].replace("</head>", head + "</head>", 1)
            page = page.replace("<body>", "<body class='browser-page'>" + bar, 1)
            response = HTMLResponse(page)
        except Exception:
            response = HTMLResponse(
                "<!doctype html><html lang='da'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>Skoleoverblik</title>{head}</head><body class='browser-page'>{bar}"
                "<main class='browser-empty'><h1>Skoleoverblik utilgængeligt</h1>"
                "<p>Data er udløbet eller mangler. Opdater for at hente et nyt overblik.</p>"
                "</main></body></html>", status_code=503)
        if owner:
            response.set_cookie("ag_csrf", csrf, secure=True, httponly=True, samesite="strict", max_age=1800)
        if clear_tracking:
            response.delete_cookie(REFRESH_COOKIE, secure=True, httponly=True, samesite="strict")
        return response

    @app.get("/preview.png")
    async def preview(request: Request):
        try:
            bundle = await run_in_threadpool(current_bundle, displays)
            simulate = request.query_params.get("preview") == "1"
            content = await run_in_threadpool(simulated_png_bytes if simulate else png_bytes, bundle)
            return Response(content, media_type="image/png")
        except Exception:
            return Response(status_code=503)

    @app.get("/board-status", response_class=HTMLResponse)
    async def board_status():
        """The board's last status report, readable while the board sleeps."""
        try:
            record = await run_in_threadpool(latest_record, statuses)
        except Exception:
            return HTMLResponse("<h1>Board status unavailable</h1>", status_code=503)
        # The scan's cost history; the page still renders without it.
        costs = await run_in_threadpool(processing_cost.history, statuses)
        # The scan's own log, folded; the page still renders without it.
        scans = await run_in_threadpool(scan_log.history, statuses)
        return HTMLResponse(render_board_status(record, processing=costs, scans=scans))

    @app.get("/panel-preview")
    async def panel_preview():
        # The panel simulation gave way to the board's own status report.
        return RedirectResponse("/board-status", status_code=303)

    @app.get("/status")
    async def status():
        from skoleklar.delivery_store import GCSDeliveryStore
        bucket = os.getenv("AG_CONTROL_BUCKET")
        if not bucket:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        try:
            result = await run_in_threadpool(GCSDeliveryStore(bucket).read_json, "latest-run.json")
            return JSONResponse(result)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/control")
    async def control(request: Request):
        return RedirectResponse("/", status_code=303)

    @app.post("/admin/refresh")
    async def trigger(request: Request):
        if request.state.user not in admins:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        supplied_origin = request.headers.get("origin")
        same_origin_fetch = request.headers.get("sec-fetch-site", "").lower() == "same-origin"
        if not origins or (supplied_origin not in origins and not same_origin_fetch):
            return JSONResponse({"error": "invalid_origin"}, status_code=403)
        from urllib.parse import parse_qs
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return Response(status_code=413)
        try:
            form = parse_qs(body.decode(), strict_parsing=True)
            csrf = form["csrf"][0]
            identifier = request_id(form["request_id"][0])
            if not csrf or not secrets.compare_digest(csrf, request.cookies.get("ag_csrf", "")):
                raise ValueError()
        except (ValueError, KeyError, UnicodeDecodeError):
            return Response(status_code=400)
        if refresh is None:
            return Response(status_code=503)
        try:
            await run_in_threadpool(refresh.trigger, "browser:" + request.state.user, identifier)
        except RefreshBusy:
            return RedirectResponse("/?refresh_notice=busy", status_code=303)
        except Exception:
            # The original ID remains inspectable after an uncertain POST; never re-dispatch it.
            pass
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(REFRESH_COOKIE, identifier, secure=True, httponly=True,
                            samesite="strict", max_age=86400)
        return response

    @app.get("/admin/refresh/{identifier}")
    async def refresh_status(request: Request, identifier: str):
        if request.state.user not in admins:
            return Response(status_code=403)
        try:
            request_id(identifier)
        except (ValueError, TypeError):
            return Response(status_code=400)
        if refresh is None:
            return Response(status_code=503)
        try:
            result = await run_in_threadpool(refresh.status, "browser:" + request.state.user, identifier)
            return JSONResponse(result or {"error": "not_found"}, status_code=200 if result else 404)
        except Exception:
            return Response(status_code=503)
    return app


app = create_app()
