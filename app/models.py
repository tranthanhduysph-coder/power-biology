from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db
import datetime

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    bot_type = db.Column(db.String(10), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    messages = db.relationship('Message', backref='author', lazy='dynamic', cascade="all, delete-orphan")
    current_session_id = db.Column(db.String(36), nullable=True)
    current_thread_id = db.Column(db.String(50), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    session_id = db.Column(db.String(36), nullable=False)

class VariableLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    session_id = db.Column(db.String(36), nullable=False)
    variable_name = db.Column(db.String(50), nullable=False)
    variable_value = db.Column(db.String(255), nullable=False)