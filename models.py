from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_verified = db.Column(db.Boolean, default=False)

    # Статус підписки
    is_pro = db.Column(db.Boolean, default=False)  # PRO користувач чи ні

    # Ліміти для Free-версії
    free_reports_used = db.Column(db.Integer, default=0)  # Кількість використаних звітів
    free_reports_reset = db.Column(db.DateTime, default=datetime.utcnow)  # Дата останнього скидання

    # 🔗 Зв'язок із Report
    reports = db.relationship('Report', backref='user', lazy=True)


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
