from app.extensions import db
from app.models import User
from app.routes import attendance as attendance_routes
from app import jwt_utils


def _make_user(app):
    with app.app_context():
        user = User(
            auth_user_id=42,
            username='player1',
            display_name='Player One',
            service_role='user',
            platform_role='user',
            claims_json={
                'memberships': [{'team_code': 'SENIORS'}],
                'teams': ['SENIORS'],
                'permissions': [],
                'member_roles': ['player'],
            },
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_current_user_uses_session_claims_for_teams(client, app, monkeypatch):
    user_id = _make_user(app)
    captured = {}

    def fake_render_template(template_name, **context):
        captured['template_name'] = template_name
        captured['context'] = context
        return 'ok'

    monkeypatch.setattr(attendance_routes, 'render_template', fake_render_template)

    with client.session_transaction() as session:
        session['user_id'] = user_id
        session['claims_json'] = {
            'memberships': [{'team_code': 'U18', 'is_active': True}],
            'teams': ['U18'],
            'permissions': [],
            'member_roles': ['player'],
        }

    response = client.get('/')

    assert response.status_code == 200
    assert captured['template_name'] == 'attendance.html'
    current_user = captured['context']['current_user']
    assert current_user['memberships'][0]['team_code'] == 'U18'
    assert current_user['teams'] == ['U18']
    assert attendance_routes._visible_team_codes(current_user) == ['U18']


def test_visible_team_codes_falls_back_to_teams_list():
    current_user = {
        'role': 'user',
        'permissions': [],
        'memberships': [],
        'teams': ['U18', 'SENIORS'],
    }

    assert attendance_routes._visible_team_codes(current_user) == ['U18', 'SENIORS']


def test_fetch_trainings_logs_and_returns_empty_list(app, monkeypatch, caplog):
    class Response:
        status_code = 503

        def json(self):
            return {}

    monkeypatch.setattr(jwt_utils.requests, 'get', lambda *args, **kwargs: Response())

    with app.app_context():
        with caplog.at_level('WARNING'):
            result = jwt_utils.fetch_trainings_from_agenda_for_teams(['seniors'])

    assert result == []
    assert any('tt-agenda trainings fetch failed' in record.message for record in caplog.records)


def test_fetch_training_detail_logs_and_returns_none(app, monkeypatch, caplog):
    def raise_request_error(*args, **kwargs):
        raise jwt_utils.requests.RequestException('boom')

    monkeypatch.setattr(jwt_utils.requests, 'get', raise_request_error)

    with app.app_context():
        with caplog.at_level('WARNING'):
            result = jwt_utils.fetch_training_occurrence_from_agenda('123', ['u18'])

    assert result is None
    assert any('tt-agenda training detail fetch failed' in record.message for record in caplog.records)
