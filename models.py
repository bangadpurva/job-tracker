from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

STATUS_RANK = {
    'applied': 1,
    'in_process': 2,
    'interview_scheduled': 3,
    'offer': 4,
    'rejected': 5,
}

TERMINAL_STATUSES = {'rejected', 'offer'}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    name = db.Column(db.String(200))
    access_token = db.Column(db.Text)
    refresh_token = db.Column(db.Text)
    token_expiry = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    applications = db.relationship('JobApplication', backref='user', lazy=True, cascade='all, delete-orphan')


class JobApplication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    gmail_message_id = db.Column(db.String(200))
    company = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(200))
    applied_date = db.Column(db.Date)
    status = db.Column(db.String(50), default='applied', nullable=False)
    interview_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    raw_email_snippet = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'gmail_message_id', name='uq_user_gmail_msg'),
    )
