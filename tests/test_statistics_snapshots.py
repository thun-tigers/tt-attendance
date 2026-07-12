from app.extensions import db
from app.models import Attendance, AttendanceEligibility
from app.statistics import summarize


def test_snapshot_counts_only_response_required_roles(app, monkeypatch):
    training = {
        'id': 'training-1',
        'team_code': 'SENIORS',
        'date': '2026-01-10',
        'category_meta': {'required_for': ['player'], 'show_presence_tracking': True},
    }
    monkeypatch.setattr(
        'app.statistics.fetch_team_members_from_auth',
        lambda team, as_of=None: [
            {'auth_user_id': 10, 'member_role': 'player', 'valid_from': '2025-01-01'},
            {'auth_user_id': 11, 'member_role': 'team_betreuer', 'valid_from': '2025-01-01'},
        ],
    )
    with app.app_context():
        db.session.add(Attendance(training_id='training-1', user_id=10, status='attending'))
        db.session.commit()
        row = summarize(training)
        assert row['eligible'] == 1
        assert row['responded'] == 1
        assert row['response_rate'] == 1.0
        assert AttendanceEligibility.query.count() == 2


def test_snapshot_marks_missing_membership_start_as_approximated(app, monkeypatch):
    training = {'id': 'training-2', 'team_code': 'SENIORS', 'date': '2026-01-10', 'category_meta': {'required_for': ['player']}}
    monkeypatch.setattr('app.statistics.fetch_team_members_from_auth', lambda team, as_of=None: [{'auth_user_id': 20, 'member_role': 'player'}])
    with app.app_context():
        row = summarize(training)
        assert row['approximate'] is True
        assert row['status']['no_response'] == 1


def test_coach_statistics_api_rejects_player(client, app):
    with client.session_transaction() as session:
        session['user_id'] = 42
    response = client.get('/api/coach/statistics/overview')
    assert response.status_code == 403
