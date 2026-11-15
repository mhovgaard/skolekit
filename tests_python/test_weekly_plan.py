import html as html_module
import json

from skolekit.parsers.weekly_plan import (
    parse_weekly_plan_html,
    segment_content,
    weekly_plan_metadata,
)


def test_parses_general_and_daily_long_form_content() -> None:
    payload = {
        "SelectedPlan": {
            "FormattedWeek": "Uge 34",
            "ClassOrGroup": "2. klasse",
            "GeneralPlan": {
                "LessonPlans": [
                    {
                        "WeeklyPlanId": 1,
                        "Subject": {"Title": "Fælles", "IsGeneralSubject": True},
                        "Content": "<p>Overall information</p>",
                    }
                ]
            },
            "DailyPlans": [
                {
                    "Day": "Onsdag",
                    "DayOfWeek": 3,
                    "Date": "2026-08-19T00:00:00",
                    "FormattedDate": "19. aug.",
                    "LessonPlans": [
                        {
                            "WeeklyPlanId": 2,
                            "Subject": {"Title": "Dansk", "LessonNumber": 1},
                            "Content": "<p>Læs side 10.<br>Lav opgave 2.</p>",
                            "Link": "/book",
                        }
                    ],
                }
            ],
        }
    }
    encoded = html_module.escape(json.dumps(payload), quote=True)
    page = f'<div id="root" data-clientlogic-settings-weeklyplansapp="{encoded}"></div>'
    url = (
        "https://x.skoleintra.dk/parent/9101/"
        "Augustitem/weeklyplansandhomework/item/class/34-2026"
    )

    result = parse_weekly_plan_html(page, url)

    assert result["week"]["number"] == 34
    assert result["general_plans"][0]["content_text"] == "Overall information"
    assert result["days"][0]["timing_defaults"]["homework"] == (
        "complete_before_source_day"
    )
    assert result["days"][0]["timing_defaults"]["lesson_preview"] == (
        "occurs_on_source_day"
    )
    assert result["days"][0]["plans"][0]["content_text"] == (
        "Læs side 10.\nLav opgave 2."
    )
    assert len(result["source_hash"]) == 64

    metadata = weekly_plan_metadata(result)
    assert metadata["days"][0]["plan_count"] == 1
    assert "content_text" not in str(metadata)


def test_segments_teacher_authored_sections_without_reclassifying_them() -> None:
    segments = segment_content(
        "Lektie\n: Læs side 10 og 11.\n"
        "Materialer: Læsebog.\n"
        "Aktivitet:\nVi arbejder videre i klassen.\n"
        "Medbring: Idrætstøj."
    )

    assert [segment["section_hint"] for segment in segments] == [
        "homework",
        "materials",
        "activity",
        "bring",
    ]
    assert segments[0]["text"] == "Læs side 10 og 11."
    assert segments[2]["text"] == "Vi arbejder videre i klassen."
