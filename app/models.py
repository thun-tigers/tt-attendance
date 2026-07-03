from datetime import datetime, timezone
from .extensions import db
from .authz import normalize_auth_payload


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    auth_user_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False)
    display_name = db.Column(db.String(120), nullable=True)
    platform_role = db.Column(db.String(32), nullable=False, default='user')
    service_role = db.Column(db.String(32), nullable=False, default='user')
    claims_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def sync_from_sso_claims(self, payload):
        auth = normalize_auth_payload(payload)
        claims = auth['claims']
        self.auth_user_id = int(claims['sub'])
        self.username = (claims.get('username') or self.username).strip()
        self.display_name = claims.get('display_name') or self.username
        self.platform_role = auth['platform_role']
        self.service_role = auth['service_role']
        self.claims_json = claims


class Attendance(db.Model):
    __tablename__ = 'attendances'
    __table_args__ = (
        db.UniqueConstraint('training_id', 'user_id', name='uq_training_user'),
    )

    id = db.Column(db.Integer, primary_key=True)
    training_id = db.Column(db.String(64), nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default='attending')
    presence_status = db.Column(db.String(16), nullable=True)
    presence_marked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'training_id': self.training_id,
            'user_id': self.user_id,
            'status': self.status,
            'presence_status': self.presence_status,
            'presence_marked_at': self.presence_marked_at.isoformat() if self.presence_marked_at else None,
            'reason': self.reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<Attendance training={self.training_id} user={self.user_id} status={self.status}>'
