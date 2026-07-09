from app.extensions import db
from app.models import Attendance, User
from scripts.seed_attendance_test_data import _seed_training, _status_counts


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
