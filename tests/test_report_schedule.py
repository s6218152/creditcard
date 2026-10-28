from datetime import datetime, timezone

import pytest

from report_schedule import should_run_report


@pytest.mark.parametrize('day,expected', [
    ('2026-10-05', True), ('2026-10-15', True), ('2026-10-25', False),
    ('2026-10-28', False), ('2026-10-29', False), ('2026-10-30', False), ('2026-10-31', True),
    ('2026-04-29', False), ('2026-04-30', True),
    ('2026-02-27', False), ('2026-02-28', True),
    ('2028-02-28', False), ('2028-02-29', True),
    ('2026-12-31', True), ('2027-01-01', False),
])
def test_scheduled_reports_run_only_on_5_15_and_month_end(day, expected):
    now = datetime.fromisoformat(day + 'T01:00:00+00:00')
    assert should_run_report('schedule', now=now) is expected


def test_schedule_uses_taiwan_calendar_instead_of_utc_calendar():
    assert should_run_report('schedule', now=datetime(2026, 2, 27, 17, tzinfo=timezone.utc))
    assert not should_run_report('schedule', now=datetime(2026, 2, 28, 17, tzinfo=timezone.utc))


def test_manual_runs_are_not_blocked_by_calendar():
    assert should_run_report('workflow_dispatch', now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert not should_run_report('push')


def test_ambiguous_timezone_is_rejected():
    with pytest.raises(ValueError, match='時區'):
        should_run_report('schedule', now=datetime(2026, 10, 31))


def test_gate_writes_github_job_output_for_manual_run(monkeypatch, tmp_path):
    import runpy
    from pathlib import Path
    output = tmp_path / 'github-output'
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    runpy.run_path(str(Path(__file__).parents[1] / 'report_schedule.py'), run_name='__main__')
    assert output.read_text() == 'run_report=true\n'
