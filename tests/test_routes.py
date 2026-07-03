from types import SimpleNamespace


def test_attendance_index_redirects_to_auth_login(client):
    response = client.get('/', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].startswith('http://localhost:8085/?next_service=tt-attendance')
    assert '/login' not in response.headers['Location']


def test_coach_dashboard_redirects_to_auth_login(client):
    response = client.get('/coach', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].startswith('http://localhost:8085/?next_service=tt-attendance')
    assert '/login' not in response.headers['Location']


def test_attendance_login_redirects_to_auth_dashboard(client):
    response = client.get('/login', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].startswith('http://localhost:8085/?next_service=tt-attendance')


def test_team_betreuer_can_open_coach_dashboard(client, monkeypatch):
    import app.routes.attendance as attendance_routes

    monkeypatch.setattr(
        attendance_routes,
        'get_current_user',
        lambda: {'id': 1, 'role': 'team_betreuer', 'username': 'betreuer-user', 'display_name': 'Betreuer User'},
    )
    monkeypatch.setattr(attendance_routes, 'fetch_trainings_from_agenda_for_teams', lambda team_codes=None: [])

    response = client.get('/coach')

    assert response.status_code == 200
    assert 'Coach-Übersicht' in response.get_data(as_text=True)


def test_attendance_summary_includes_open_count(client, monkeypatch):
    import app.routes.api as attendance_api

    monkeypatch.setattr(attendance_api, 'get_current_user', lambda: {'id': 1, 'role': 'user'})
    monkeypatch.setattr(
        attendance_api,
        'fetch_training_occurrence_from_agenda',
        lambda occurrence_id: {'id': occurrence_id, 'team_code': 'U16'},
    )

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith('/api/internal/teams/U16/active-member-count')
        return SimpleNamespace(status_code=200, json=lambda: {'active_member_count': 5})

    monkeypatch.setattr(attendance_api.requests, 'get', fake_get)

    response = client.get('/api/trainings/test-occurrence/attendance')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['summary']['open'] == 5


def test_coach_dashboard_renders_open_count(client, app, monkeypatch):
    import app.routes.attendance as attendance_routes
    from app.extensions import db
    from app.models import Attendance

    monkeypatch.setattr(
        attendance_routes,
        'get_current_user',
        lambda: {'id': 1, 'role': 'coach', 'username': 'coach-user', 'display_name': 'Coach User'},
    )
    monkeypatch.setattr(
        attendance_routes,
        'fetch_trainings_from_agenda_for_teams',
        lambda team_codes=None: [{'id': 'training-1', 'title': 'Team Training', 'team_code': 'U16', 'date': '2026-07-01', 'time': '19:00'}],
    )
    monkeypatch.setattr(
        attendance_routes,
        '_fetch_position_group_defs',
        lambda: [
            {'key': 'LB', 'label': 'LB', 'sort_order': 1},
            {'key': 'RB', 'label': 'RB', 'sort_order': 2},
            {'key': 'QB', 'label': 'QB', 'sort_order': 3},
        ],
    )
    monkeypatch.setattr(
        attendance_routes,
        '_fetch_team_player_roster',
        lambda team_code: [
            {'id': 11, 'auth_user_id': 11, 'username': 'player-11', 'display_name': 'Player 11', 'first_name': 'Player', 'last_name': '11'},
            {'id': 12, 'auth_user_id': 12, 'username': 'player-12', 'display_name': 'Player 12', 'first_name': 'Player', 'last_name': '12'},
            {'id': 13, 'auth_user_id': 13, 'username': 'player-13', 'display_name': 'Player 13', 'first_name': 'Player', 'last_name': '13'},
            {'id': 14, 'auth_user_id': 14, 'username': 'player-14', 'display_name': 'Player 14', 'first_name': 'Player', 'last_name': '14'},
            {'id': 15, 'auth_user_id': 15, 'username': 'player-15', 'display_name': 'Player 15', 'first_name': 'Player', 'last_name': '15'},
        ],
    )
    monkeypatch.setattr(
        attendance_routes,
        '_fetch_member_profile',
        lambda auth_user_id: {
            11: {'position': 'LB'},
            12: {'position': 'LB'},
            13: {'position': 'RB'},
            14: {'position': 'QB'},
            15: {'position': 'QB'},
        }.get(auth_user_id, {}),
    )

    with app.app_context():
        db.session.add(Attendance(training_id='training-1', user_id=11, status='attending'))
        db.session.add(Attendance(training_id='training-1', user_id=12, status='maybe'))
        db.session.add(Attendance(training_id='training-1', user_id=13, status='declined'))
        db.session.commit()

    response = client.get('/coach')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'LB' in html
    assert 'RB' in html
    assert 'QB' in html
    assert 'Angemeldet' not in html


def test_coach_detail_renders_participant_rows(client, app, monkeypatch):
    import app.routes.attendance as attendance_routes
    from app.extensions import db
    from app.models import Attendance

    monkeypatch.setattr(
        attendance_routes,
        'get_current_user',
        lambda: {'id': 1, 'role': 'coach', 'username': 'coach-user', 'display_name': 'Coach User'},
    )
    monkeypatch.setattr(
        attendance_routes,
        'fetch_training_occurrence_from_agenda',
        lambda occurrence_id: {'id': occurrence_id, 'title': 'Team Training', 'team_code': 'U16'},
    )
    monkeypatch.setattr(
        attendance_routes,
        '_fetch_position_group_defs',
        lambda: [
            {'key': 'LB', 'label': 'LB', 'sort_order': 1},
            {'key': 'RB', 'label': 'RB', 'sort_order': 2},
        ],
    )
    monkeypatch.setattr(
        attendance_routes,
        '_fetch_team_player_roster',
        lambda team_code: [
            {'id': 11, 'auth_user_id': 11, 'username': 'user-11', 'display_name': 'User 11'},
            {'id': 12, 'auth_user_id': 12, 'username': 'user-12', 'display_name': 'User 12'},
            {'id': 13, 'auth_user_id': 13, 'username': 'user-13', 'display_name': 'User 13'},
        ],
    )
    monkeypatch.setattr(
        attendance_routes,
        '_fetch_member_profile',
        lambda auth_user_id: {
            11: {'position': 'LB'},
            12: {'position': 'RB'},
            13: {'position': 'RB'},
        }.get(auth_user_id, {}),
    )

    with app.app_context():
        db.session.add(Attendance(training_id='training-1', user_id=11, status='attending'))
        db.session.add(Attendance(training_id='training-1', user_id=12, status='maybe'))
        db.session.commit()

    response = client.get('/coach/training/training-1')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'WhatsApp-Export' not in html
    assert 'LB' in html
    assert 'RB' in html
    assert 'Dabei' in html
    assert 'Unsicher' in html
    assert 'Abgesagt' in html
    assert 'Offen' in html


def test_coach_can_set_participant_status(client, app, monkeypatch):
    import app.routes.attendance as attendance_routes
    from app.extensions import db
    from app.models import Attendance

    monkeypatch.setattr(
        attendance_routes,
        'get_current_user',
        lambda: {
            'id': 1,
            'role': 'coach',
            'username': 'coach-user',
            'display_name': 'Coach User',
            'memberships': [{'team_code': 'U16', 'member_role': 'coach', 'is_active': True}],
        },
    )
    monkeypatch.setattr(
        attendance_routes,
        'fetch_training_occurrence_from_agenda',
        lambda occurrence_id: {'id': occurrence_id, 'title': 'Team Training', 'team_code': 'U16'},
    )
    monkeypatch.setattr(
        attendance_routes,
        '_fetch_team_player_roster',
        lambda team_code: [{'id': 11, 'auth_user_id': 11, 'username': 'user-11', 'display_name': 'User 11'}],
    )

    with app.app_context():
        db.session.add(Attendance(training_id='training-1', user_id=11, status='attending'))
        db.session.commit()

    response = client.post(
        '/api/coach/trainings/training-1/participants/11/status',
        json={'status': 'open'},
    )

    assert response.status_code == 200
    assert response.get_json()['attendance'] is None
