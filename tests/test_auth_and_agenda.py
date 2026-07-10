from app.extensions import db
from app.models import Attendance, User
from app import attendance_summary
from app.routes import api as api_routes
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


def _make_coach(app):
    with app.app_context():
        user = User(
            auth_user_id=77,
            username='coach1',
            display_name='Coach One',
            service_role='coach',
            platform_role='user',
            claims_json={
                'memberships': [{'team_code': 'SENIORS'}],
                'teams': ['SENIORS'],
                'permissions': [],
                'member_roles': ['coach'],
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
    monkeypatch.setattr(attendance_routes, 'fetch_position_groups', lambda: [])
    monkeypatch.setattr(
        attendance_routes,
        'summarize_training_attendance',
        lambda training_id, position_groups=None: {'position_summary': []},
    )

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


def test_attendance_index_renders_with_session_user(client, app, monkeypatch):
    user_id = _make_user(app)

    monkeypatch.setattr(attendance_routes, 'fetch_position_groups', lambda: [])
    monkeypatch.setattr(attendance_routes, 'fetch_trainings_from_agenda_for_teams', lambda team_codes=None, limit=None: [])
    monkeypatch.setattr(
        attendance_routes,
        'render_template',
        lambda template_name, **context: f'rendered:{template_name}',
    )

    with client.session_transaction() as session:
        session['user_id'] = user_id
        session['claims_json'] = {
            'memberships': [{'team_code': 'SENIORS', 'is_active': True}],
            'teams': ['SENIORS'],
            'permissions': [],
            'member_roles': ['player'],
        }

    response = client.get('/')

    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'rendered:attendance.html'


def test_visible_team_codes_falls_back_to_teams_list():
    current_user = {
        'role': 'user',
        'permissions': [],
        'memberships': [],
        'teams': ['U18', 'SENIORS'],
    }

    assert attendance_routes._visible_team_codes(current_user) == ['U18', 'SENIORS']


def test_position_summary_counts_only_attending_users(app, monkeypatch):
    with app.app_context():
        db.session.add_all([
            Attendance(training_id='training-1', user_id=10, status='attending'),
            Attendance(training_id='training-1', user_id=11, status='attending'),
            Attendance(training_id='training-1', user_id=12, status='maybe'),
            Attendance(training_id='training-1', user_id=13, status='declined'),
        ])
        db.session.commit()

        positions_by_user = {
            10: 'OL',
            11: 'QB',
            12: 'OL',
            13: 'QB',
        }
        monkeypatch.setattr(attendance_summary, 'fetch_member_position', lambda user_id: positions_by_user.get(user_id))

        result = attendance_summary.summarize_training_attendance(
            'training-1',
            [
                {'key': 'OL', 'label': 'Offense Line'},
                {'key': 'QB', 'label': 'Quarterback'},
            ],
        )

    assert result['summary'] == {'attending': 2, 'maybe': 1, 'declined': 1}
    assert result['position_summary'] == [
        {'key': 'OL', 'label': 'Offense Line', 'attending': 1},
        {'key': 'QB', 'label': 'Quarterback', 'attending': 1},
    ]


def test_attendance_card_renders_time_in_header_and_position_badges(client, app, monkeypatch):
    user_id = _make_coach(app)
    monkeypatch.setattr(attendance_routes, 'fetch_position_groups', lambda: [{'key': 'OL', 'label': 'Offense Line'}])
    monkeypatch.setattr(attendance_routes, 'fetch_trainings_from_agenda_for_teams', lambda team_codes=None, limit=None: [{
        'id': 'training-1',
        'title': 'Sommertraining MI',
        'team_code': 'SENIORS',
        'date': '2026-07-08',
        'start_time': '19:30',
        'end_time': '21:30',
        'location': 'Stadion',
    }])
    monkeypatch.setattr(
        attendance_routes,
        'summarize_training_attendance',
        lambda training_id, position_groups=None: {
            'position_summary': [{'key': 'OL', 'label': 'Offense Line', 'attending': 3}],
        },
    )

    with client.session_transaction() as session:
        session['user_id'] = user_id

    response = client.get('/')

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Sommertraining MI' in body
    assert '19:30' in body
    assert '21:30' in body
    assert 'OL' in body
    assert 'Offense Line' not in body
    assert 'position-count' in body
    assert '/coach/training/training-1' in body


def test_coach_entry_redirects_to_next_training_and_statistics_has_own_route(client, app, monkeypatch):
    coach_id = _make_coach(app)
    monkeypatch.setattr(attendance_routes, 'fetch_trainings_from_agenda_for_teams', lambda team_codes=None, limit=None: [
        {'id': 'training-1', 'title': 'Nächstes Training', 'is_cancelled': False},
    ])

    with client.session_transaction() as session:
        session['user_id'] = coach_id

    response = client.get('/coach')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/coach/training/training-1')

    statistics_response = client.get('/coach/statistics')
    assert statistics_response.status_code == 200
    assert 'Coach-Statistik' in statistics_response.get_data(as_text=True)


def test_attendance_index_preloads_all_visible_training_summaries(client, app, monkeypatch):
    user_id = _make_user(app)
    summary_calls = []
    captured = {}

    monkeypatch.setattr(attendance_routes, 'fetch_position_groups', lambda: [{'key': 'OL', 'label': 'Offense Line'}])
    monkeypatch.setattr(attendance_routes, 'fetch_trainings_from_agenda_for_teams', lambda team_codes=None, limit=None: [
        {
            'id': 'training-1',
            'title': 'Erstes Training',
            'team_code': 'SENIORS',
            'date': '2026-07-08',
            'start_time': '19:30',
            'end_time': '21:00',
            'location': 'Stadion',
        },
        {
            'id': 'training-2',
            'title': 'Zweites Training',
            'team_code': 'SENIORS',
            'date': '2026-07-10',
            'start_time': '19:30',
            'end_time': '21:00',
            'location': 'Stadion',
        },
    ])
    monkeypatch.setattr(
        attendance_routes,
        'summarize_training_attendance',
        lambda training_id, position_groups=None: summary_calls.append(training_id) or {
            'position_summary': [{'key': 'OL', 'label': 'Offense Line', 'attending': 3}],
        },
    )
    monkeypatch.setattr(
        attendance_routes,
        'render_template',
        lambda template_name, **context: captured.update({'template_name': template_name, 'context': context}) or 'rendered',
    )

    with client.session_transaction() as session:
        session['user_id'] = user_id
        session['claims_json'] = {
            'memberships': [{'team_code': 'SENIORS', 'is_active': True}],
            'teams': ['SENIORS'],
            'permissions': [],
            'member_roles': ['player'],
        }

    response = client.get('/')

    assert response.status_code == 200
    context = captured['context']
    assert captured['template_name'] == 'attendance.html'
    assert summary_calls == ['training-1', 'training-2']
    assert context['trainings'][0]['summary_loaded'] is True
    assert context['trainings'][0]['position_summary'] == [{'key': 'OL', 'label': 'Offense Line', 'attending': 3}]
    assert context['trainings'][1]['summary_loaded'] is True
    assert context['trainings'][1]['position_summary'] == [{'key': 'OL', 'label': 'Offense Line', 'attending': 3}]


def test_attendance_summary_only_skips_participants(client, app, monkeypatch):
    coach_id = _make_coach(app)
    with app.app_context():
        db.session.add(Attendance(training_id='training-1', user_id=10, status='attending'))
        db.session.commit()

    monkeypatch.setattr(api_routes, 'fetch_training_occurrence_from_agenda', lambda occurrence_id: {'id': occurrence_id, 'is_cancelled': False})
    monkeypatch.setattr(api_routes, 'fetch_user_from_auth', lambda user_id: (_ for _ in ()).throw(AssertionError('should not fetch participants')))
    monkeypatch.setattr(api_routes, 'summarize_training_attendance', lambda training_id, position_groups=None: {
        'attendances': [Attendance(training_id='training-1', user_id=10, status='attending')],
        'summary': {'attending': 1, 'maybe': 0, 'declined': 0},
        'position_summary': [{'key': 'OL', 'label': 'Offense Line', 'attending': 1}],
    })

    with client.session_transaction() as session:
        session['user_id'] = coach_id

    response = client.get('/api/trainings/training-1/attendance?summary_only=1')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['summary'] == {'attending': 1, 'maybe': 0, 'declined': 0}
    assert payload['position_summary'] == [{'key': 'OL', 'label': 'Offense Line', 'attending': 1}]
    assert 'participants' not in payload


def test_attendance_deferred_trainings_returns_fragment(client, app, monkeypatch):
    user_id = _make_user(app)

    monkeypatch.setattr(attendance_routes, 'fetch_position_groups', lambda: [])
    monkeypatch.setattr(attendance_routes, 'fetch_trainings_from_agenda_for_teams', lambda team_codes=None, limit=None: [
        {'id': 'training-1', 'title': 'Erstes Training', 'team_code': 'SENIORS', 'date': '2026-07-08', 'start_time': '19:30', 'end_time': '21:00', 'location': 'Stadion'},
        {'id': 'training-2', 'title': 'Zweites Training', 'team_code': 'SENIORS', 'date': '2026-07-10', 'start_time': '19:30', 'end_time': '21:00', 'location': 'Stadion'},
    ])
    monkeypatch.setattr(attendance_routes, 'render_template', lambda template_name, **context: f"<div class='training-card'>{context['t']['title']}</div>")

    with client.session_transaction() as session:
        session['user_id'] = user_id
        session['claims_json'] = {
            'memberships': [{'team_code': 'SENIORS', 'is_active': True}],
            'teams': ['SENIORS'],
            'permissions': [],
            'member_roles': ['player'],
        }

    response = client.get('/api/trainings/deferred')

    assert response.status_code == 200
    payload = response.get_json()
    assert 'Zweites Training' in payload['html']
    assert 'Erstes Training' not in payload['html']
    assert payload['has_more'] is False


def test_attendance_deferred_trainings_supports_batched_loading(client, app, monkeypatch):
    user_id = _make_user(app)

    monkeypatch.setattr(attendance_routes, 'fetch_position_groups', lambda: [])
    monkeypatch.setattr(attendance_routes, 'fetch_trainings_from_agenda_for_teams', lambda team_codes=None, limit=None: [
        {'id': 'training-1', 'title': 'Erstes Training', 'team_code': 'SENIORS', 'date': '2026-07-08', 'start_time': '19:30', 'end_time': '21:00', 'location': 'Stadion'},
        {'id': 'training-2', 'title': 'Zweites Training', 'team_code': 'SENIORS', 'date': '2026-07-10', 'start_time': '19:30', 'end_time': '21:00', 'location': 'Stadion'},
        {'id': 'training-3', 'title': 'Drittes Training', 'team_code': 'SENIORS', 'date': '2026-07-12', 'start_time': '19:30', 'end_time': '21:00', 'location': 'Stadion'},
        {'id': 'training-4', 'title': 'Viertes Training', 'team_code': 'SENIORS', 'date': '2026-07-14', 'start_time': '19:30', 'end_time': '21:00', 'location': 'Stadion'},
    ])
    monkeypatch.setattr(attendance_routes, 'render_template', lambda template_name, **context: f"<div class='training-card'>{context['t']['title']}</div>")

    with client.session_transaction() as session:
        session['user_id'] = user_id
        session['claims_json'] = {
            'memberships': [{'team_code': 'SENIORS', 'is_active': True}],
            'teams': ['SENIORS'],
            'permissions': [],
            'member_roles': ['player'],
        }

    response = client.get('/api/trainings/deferred?offset=0&limit=2')

    assert response.status_code == 200
    payload = response.get_json()
    assert 'Zweites Training' in payload['html']
    assert 'Drittes Training' in payload['html']
    assert 'Viertes Training' not in payload['html']
    assert payload['count'] == 2
    assert payload['has_more'] is True


def test_coach_presence_api_marks_user_attending(client, app):
    coach_id = _make_coach(app)
    with app.app_context():
        db.session.add(Attendance(training_id='training-1', user_id=10, status='maybe'))
        db.session.commit()

    with client.session_transaction() as session:
        session['user_id'] = coach_id

    response = client.post('/api/trainings/training-1/presence', json={
        'user_id': 10,
        'attendance_status': 'attending',
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['attendance_status'] == 'attending'
    assert payload['presence_status'] == 'present'
    assert payload['presence_summary'] == {'present': 1, 'unexcused': 0}

    with app.app_context():
        attendance = Attendance.query.filter_by(training_id='training-1', user_id=10).first()
        assert attendance.status == 'attending'
        assert attendance.presence_status == 'present'
        assert attendance.presence_marked_at is not None


def test_coach_presence_api_marks_user_unexcused_as_declined(client, app):
    coach_id = _make_coach(app)
    with app.app_context():
        db.session.add(Attendance(training_id='training-1', user_id=10, status='attending', presence_status='present'))
        db.session.commit()

    with client.session_transaction() as session:
        session['user_id'] = coach_id

    response = client.post('/api/trainings/training-1/presence', json={
        'user_id': 10,
        'attendance_status': 'unexcused',
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['attendance_status'] == 'declined'
    assert payload['presence_status'] == 'unexcused'
    assert payload['summary'] == {'attending': 0, 'maybe': 0, 'declined': 1}

    with app.app_context():
        attendance = Attendance.query.filter_by(training_id='training-1', user_id=10).first()
        assert attendance.status == 'declined'
        assert attendance.presence_status == 'unexcused'


def test_player_status_requires_reason_for_maybe_and_declined(client, app, monkeypatch):
    user_id = _make_user(app)
    monkeypatch.setattr(attendance_routes, 'fetch_training_occurrence_from_agenda', lambda occurrence_id: {
        'id': occurrence_id,
        'is_cancelled': False,
    })
    monkeypatch.setattr(attendance_routes, 'summarize_training_attendance', lambda occurrence_id: {
        'summary': {'attending': 0, 'maybe': 0, 'declined': 1},
        'position_summary': [],
    })

    with client.session_transaction() as session:
        session['user_id'] = user_id

    missing_reason = client.post('/api/trainings/training-1/set-status', json={'status': 'declined'})
    assert missing_reason.status_code == 400
    assert missing_reason.get_json()['error'] == 'reason_required'

    saved = client.post('/api/trainings/training-1/set-status', json={
        'status': 'declined',
        'reason': 'Verletzt',
    })
    assert saved.status_code == 200

    with app.app_context():
        attendance = Attendance.query.filter_by(training_id='training-1', user_id=42).first()
        assert attendance.reason == 'Verletzt'


def test_coach_detail_renders_presence_controls(client, app, monkeypatch):
    coach_id = _make_coach(app)
    with app.app_context():
        db.session.add_all([
            Attendance(training_id='training-1', user_id=10, status='attending'),
            Attendance(training_id='training-1', user_id=11, status='maybe'),
            Attendance(training_id='training-1', user_id=12, status='declined'),
        ])
        db.session.commit()

    monkeypatch.setattr(attendance_routes, 'fetch_position_groups', lambda: [{'key': 'OL', 'label': 'Offense Line'}])
    monkeypatch.setattr(attendance_routes, 'fetch_member_position', lambda user_id: 'OL')
    monkeypatch.setattr(attendance_routes, 'fetch_user_from_auth', lambda user_id: {
        'username': f'user{user_id}',
        'display_name': f'User {user_id}',
    })
    monkeypatch.setattr(attendance_routes.requests, 'get', lambda *args, **kwargs: type('Response', (), {
        'status_code': 200,
        'json': lambda self: {'id': 'training-1', 'title': 'Training', 'date': '2026-07-10', 'time': '10:00 - 12:00'},
    })())

    with client.session_transaction() as session:
        session['user_id'] = coach_id

    response = client.get('/coach/training/training-1')

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Präsenzkontrolle' in body
    assert 'OL' in body
    assert 'Zugesagt' in body
    assert 'Unsicher' in body
    assert 'Abgesagt' in body
    assert 'Vorname' in body
    assert 'Nachname' in body
    assert 'js-smart-table' in body
    assert 'OK' in body
    assert 'Unentschuldigt' in body


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
