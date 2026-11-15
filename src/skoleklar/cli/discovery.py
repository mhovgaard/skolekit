"""Command helpers for discovery."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page, Response

from skolekit.auth.browser import close_context, first_page, open_context
from skolekit.state import write_private_json
from skolekit.transport import sanitize_url
from skolekit.transport import is_allowed_url, is_authenticated_page
from skoleklar.cli.common import config_root, json_shape, target_url


async def summarize_response(response: Response) -> dict[str, Any] | None:
    if not is_allowed_url(response.url):
        return None
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    item: dict[str, Any] = {
        "kind": "response",
        "method": response.request.method,
        "resource_type": response.request.resource_type,
        "status": response.status,
        "content_type": content_type,
        "url": sanitize_url(response.url),
        "api_candidate": (
            response.request.resource_type in {"xhr", "fetch"}
            or content_type == "application/json"
        ),
    }
    content_length = response.headers.get("content-length")
    if content_length and content_length.isdigit():
        item["content_length"] = int(content_length)
    if content_type == "application/json":
        try:
            item["json_shape"] = json_shape(await response.json())
        except Exception:
            item["json_shape"] = "unavailable"
    return item


async def page_summary(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """() => ({
          url: location.href,
          title: document.title,
          links: Array.from(document.querySelectorAll('a[href]'))
            .slice(0, 150)
            .map((a) => new URL(a.getAttribute('href'), location.href).href),
          forms: Array.from(document.querySelectorAll('form'))
            .slice(0, 30)
            .map((form) => ({
              method: (form.method || 'get').toUpperCase(),
              action: new URL(form.action || location.href, location.href).href
            }))
        })"""
    )


async def command_discover(args: argparse.Namespace) -> int:
    target = target_url(args)
    runner, context = await open_context(headless=not args.headed)
    response_tasks: list[asyncio.Task[dict[str, Any] | None]] = []
    request_items: list[dict[str, Any]] = []

    def on_request(request: Any) -> None:
        if not is_allowed_url(request.url):
            return
        request_items.append(
            {
                "kind": "request",
                "method": request.method,
                "resource_type": request.resource_type,
                "url": sanitize_url(request.url),
            }
        )

    def on_response(response: Response) -> None:
        response_tasks.append(asyncio.create_task(summarize_response(response)))

    context.on("request", on_request)
    context.on("response", on_response)

    try:
        page = await first_page(context)
        response = await page.goto(target, wait_until="domcontentloaded")
        await page.wait_for_timeout(args.duration * 1000)

        if not is_authenticated_page(page.url):
            print(
                "The session is not authenticated. Run `uv run skoleklar login` first.",
                file=sys.stderr,
            )
            return 2

        summarized = await asyncio.gather(*response_tasks, return_exceptions=False)
        responses = [item for item in summarized if item is not None]
        page_data = await page_summary(page)
        page_data["url"] = sanitize_url(page_data["url"])
        page_data["links"] = sorted(
            {
                sanitize_url(link)
                for link in page_data["links"]
                if is_allowed_url(link)
            }
        )
        page_data["forms"] = [
            {**form, "action": sanitize_url(form["action"])}
            for form in page_data["forms"]
            if is_allowed_url(form["action"])
        ]

        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "target": sanitize_url(target),
            "initial_status": response.status if response else None,
            "page": page_data,
            "traffic": request_items + responses,
        }
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        report_path = config_root() / "discoveries" / f"discovery-{stamp}.json"
        write_private_json(report_path, report)

        api_candidates = [item for item in responses if item["api_candidate"]]
        print(f"Discovery saved to {report_path}")
        print(f"Captured {len(responses)} responses; {len(api_candidates)} API candidates.")
        for item in api_candidates[:20]:
            print(f"{item['status']} {item['method']} {item['url']}")
        return 0
    finally:
        await close_context(runner, context)
