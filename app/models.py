from datetime import datetime, timezone
from .extensions import db


class Attendance(db.Model):
    __tablename__ = 'attendances'
    __table_args__ = (
        db.UniqueConstraint('training_id', 'user_id', name='uq_training_user'),
    )

    id = db.Column(db.Integer, primary_key=True)
    training_id = db.Column(db.String(64), nullable=False, index=True)  # Training occurrence ID from tt-agenda
    user_id = db.Column(db.Integer, nullable=False, index=True)  # User ID from tt-auth
    status = db.Column(db.String(16), nullable=False, default='attending')  # attending, maybe, declined
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'training_id': self.training_id,
            'user_id': self.user_id,
            'status': self.status,
            'reason': self.reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<Attendance training={self.training_id} user={self.user_id} status={self.status}>'
