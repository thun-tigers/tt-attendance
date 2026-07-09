from app.extensions import db
from app.models import Attendance, User
from scripts.seed_attendance_test_data import _fetch_members_from_auth, _seed_training, _status_counts


def test_status_counts_match_requested_ratio():
    assert _status_counts(10) == {'attending': 7, 'maybe': 1, 'declined': 2}
    assert _status_counts(3) == {'attending': 2, 'maybe': 0, 'declined': 1}
    assert _status_counts(1) == {'attending': 1, 'maybe': 0, 'declined': 0}


def test_seed_training_writes_expected_distribution(app):
    with app.app_context():
        users = []
        for index in range(10):
            user = User(
                auth_user_id=1000 + index,
                username=f'user{index}',
                display_name=f'User {index}',
                service_role='user',
                platform_role='user',
                claims_json={},
            )
            db.session.add(user)
            users.append(user)
        db.session.commit()

        result = _seed_training({'id': 'training-1', 'title': 'Test Training'}, users, seed=42, clear_existing=True)
        db.session.commit()

        attendances = Attendance.query.filter_by(training_id='training-1').all()

    assert result.counts == {'attending': 7, 'maybe': 1, 'declined': 2}
    assert len(attendances) == 10
    assert sum(1 for attendance in attendances if attendance.status == 'attending') == 7
    assert sum(1 for attendance in attendances if attendance.status == 'maybe') == 1
    assert sum(1 for attendance in attendances if attendance.status == 'declined') == 2
    assert all(attendance.reason is None for attendance in attendances)


def test_seed_training_accepts_auth_member_dicts(app):
    with app.app_context():
        members = [
            {'id': 2001, 'username': 'member1', 'display_name': 'Member 1'},
            {'id': 2002, 'username': 'member2', 'display_name': 'Member 2'},
            {'id': 2003, 'username': 'member3', 'display_name': 'Member 3'},
            {'id': 2004, 'username': 'member4', 'display_name': 'Member 4'},
        ]

        result = _seed_training({'id': 'training-2', 'title': 'Auth Members'}, members, seed=7, clear_existing=True)
        db.session.commit()

        attendances = Attendance.query.filter_by(training_id='training-2').all()
        user_ids = sorted(attendance.user_id for attendance in attendances)

    assert result.total_users == 4
    assert user_ids == [2001, 2002, 2003, 2004]
    assert sum(1 for attendance in attendances if attendance.status == 'attending') == 3
    assert sum(1 for attendance in attendances if attendance.status == 'maybe') == 0
    assert sum(1 for attendance in attendances if attendance.status == 'declined') == 1


def test_fetch_members_from_auth_uses_team_manager_endpoint(app, monkeypatch):
    import scripts.seed_attendance_test_data as seed_script

    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    def fake_request(method, path, *, params=None, json=None):
        calls.append((method, path, params))
        approver_id = params.get('approver_auth_user_id') if params else None
        if approver_id == 1:
            return FakeResponse(403, {'error': 'forbidden'}), None
        if approver_id == 2:
            return FakeResponse(200, {
                'users': [
                    {'id': 3001, 'username': 'member-a', 'display_name': 'Member A'},
                    {'id': 3002, 'username': 'member-b', 'display_name': 'Member B'},
                ]
            }), None
        return FakeResponse(404, {'error': 'not_found'}), None

    monkeypatch.setattr(seed_script, '_auth_internal_request', fake_request)

    with app.app_context():
        members = _fetch_members_from_auth()

    assert [member['id'] for member in members] == [3001, 3002]
    assert calls[0][2]['approver_auth_user_id'] == 1
    assert calls[1][2]['approver_auth_user_id'] == 2
