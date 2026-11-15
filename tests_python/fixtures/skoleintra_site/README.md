# Synthetic SkoleIntra site

Fixtures for `tests_python/skoleintra_site.py`, which serves them through
`httpx.MockTransport` so the adapters in `skolekit.sources` and household harvest in `skoleklar.harvest` can be tested
without the network.

**Everything here is invented.** The structure follows the real site: the
routes, the `.sk-news-item` markup, the forms' `data-vue-component-data`
payload, the weekly-plan and inbox settings attributes, and the calendar JSON
feeds. The content does not: the children `Alfa` (9101, class `001`) and `Beta`
(9202, class `003`), the staff, the notices, the plans, the events and the
messages are all made up. Never paste a real page, a real child's name or a
surname here. `test_site_collectors.py` fails if the household's own children,
IDs or school host appear in any fixture.

Layout:

- `alfa/`, `beta/`: one directory per child, named by the files the site
  answers with (`index.html`, `forms-open.json`, `calendar-LessonsEvents.json`
  and so on). Alfa's site is the rich one: a paged front page, open and closed
  forms with an answer page, three weekly plans, all three calendar feeds and a
  synthetic inbox with paging. Beta's is small.
- `forms-list.html`, `weekly-plan.html`, `messages-inbox.html`: page templates
  shared by both children. `{{payload}}` is filled with the child's JSON file,
  HTML-escaped as the site embeds it in an attribute.
- `{{student_id}}` and `{{child}}` are filled per child in every file.
- `showmore.json` and the `messages-*.json` files script the JSON endpoints:
  which response answers which cursor, thread or message ID.

The site answers 404 to an unknown route, 400 to an unexpected cursor or
calendar parameter, and 403/405 to any write endpoint or other POST, so a
collector that changes its requests fails its test.
