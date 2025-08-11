from flask import Flask, request, render_template, send_file, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
import openai
import pdfkit
import os
import csv
import io
import json
from datetime import datetime, timedelta
from models import db, User

app = Flask(__name__)

# === CONFIG (MAIL + CORE) ===
app.config.update(
    MAIL_SERVER="smtp.gmail.com",
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.environ.get("EMAIL_USER"),
    MAIL_PASSWORD=os.environ.get("EMAIL_PASS"),
    MAIL_DEFAULT_SENDER=(
        os.environ.get("MAIL_DEFAULT_SENDER") or os.environ.get("EMAIL_USER")
    ),
    SECRET_KEY=os.environ.get("SECRET_KEY", "mysecret"),
    SQLALCHEMY_DATABASE_URI="sqlite:///users.db",
)

# Санітарна перевірка, щоб не ловити 500 на POST /register
for k in ("MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_DEFAULT_SENDER"):
    if not app.config.get(k):
        raise RuntimeError(f"Missing required mail config: {k}")

# === INIT ===
mail = Mail(app)
db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

with app.app_context():
    db.create_all()

openai.api_key = os.getenv("OPENAI_API_KEY")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# === ROUTES ===
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        hashed_pw = generate_password_hash(password, method="pbkdf2:sha256")

        if User.query.filter_by(email=email).first():
            return "User already exists"

        new_user = User(email=email, password=hashed_pw, is_verified=False)
        db.session.add(new_user)
        db.session.commit()

        # Email confirmation
        s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
        token = s.dumps(email, salt="email-confirm")
        link = url_for('confirm_email', token=token, _external=True)

        msg = Message(
            "Confirm your email",
            sender=app.config["MAIL_DEFAULT_SENDER"],  # ключове
            recipients=[email],
        )
        msg.html = f"""
        <h3>Welcome to RestGenius!</h3>
        <p>Click the button below to verify your email and start using your account:</p>
        <a href="{link}" style="padding: 10px 20px; background: #1a73e8; color: white; text-decoration: none; border-radius: 6px;">✅ Confirm Email</a>
        """

        try:
            mail.send(msg)
        except Exception as e:
            app.logger.exception("Mail send failed")
            # Можеш повернути дружнє повідомлення або флеш
            return "We couldn't send the confirmation email right now. Please try again later.", 200

        return "✅ Registration successful. Please check your email to confirm."

    return render_template("register.html")

@app.route("/confirm/<token>")
def confirm_email(token):
    try:
        s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
        email = s.loads(token, salt="email-confirm", max_age=3600)
        user = User.query.filter_by(email=email).first()
        if not user:
            return "User not found."
        user.is_verified = True
        db.session.commit()
        return "✅ Email confirmed! You can now log in."
    except Exception as e:
        return f"❌ Invalid or expired link: {e}"

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            return "Invalid credentials"
        if not user.is_verified:
            return "❗ Please verify your email before logging in."
        login_user(user)
        return "Login successful"
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return "Logged out successfully"

from models import Report  # якщо ще не імпортовано

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    if not current_user.is_verified:
        return "❌ Please verify your email before using this feature.", 403

    if 'file' not in request.files:
        return "No file uploaded", 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        return "File must be a CSV", 400

    user_email = current_user.email
    is_pro = user_email.endswith("@pro.com")

    # === Зчитування usage.json ===
    usage_path = "usage.json"
    if os.path.exists(usage_path):
        with open(usage_path, "r") as f:
            usage_data = json.load(f)
    else:
        usage_data = {}

    now = datetime.now()
    user_record = usage_data.get(user_email, {"reports": 0, "last_reset": now.isoformat()})
    if now - datetime.fromisoformat(user_record["last_reset"]) > timedelta(days=14):
        user_record = {"reports": 0, "last_reset": now.isoformat()}

    if not is_pro and user_record["reports"] >= 3:
        return "<h2>❌ Free Limit Reached</h2><p>Please upgrade to PRO.</p>", 403

    try:
        # === Обробка CSV ===
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        rows = list(csv.reader(stream))
        sales_data = "\n".join([", ".join(row) for row in rows])

        # === GPT: Основний запит ===
        main_prompt = f"""You're an expert restaurant consultant. Analyze the sales data:\n\n{sales_data}"""
        chat_completion = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": main_prompt}]
        )
        result = chat_completion.choices[0].message.content.strip()

        roi_forecast, top_campaign = "", ""
        if is_pro:
            roi_prompt = f"ROI prediction:\n{sales_data}"
            campaign_prompt = f"Suggest a campaign:\n{sales_data}"

            roi_forecast = openai.chat.completions.create(
                model="gpt-3.5-turbo", messages=[{"role": "user", "content": roi_prompt}]
            ).choices[0].message.content.strip()

            top_campaign = openai.chat.completions.create(
                model="gpt-3.5-turbo", messages=[{"role": "user", "content": campaign_prompt}]
            ).choices[0].message.content.strip()

        # === Рендер HTML ===
        html = render_template("report.html",
                               content=result,
                               is_pro=is_pro,
                               roi_forecast=roi_forecast,
                               top_campaign=top_campaign)

        # === Створення PDF ===
        reports_dir = "reports"
        os.makedirs(reports_dir, exist_ok=True)
        filename = f"report_{now.strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
        pdf_path = os.path.join(reports_dir, filename)
        pdfkit.from_string(html, pdf_path)

        # === Збереження звіту в базу ===
        new_report = Report(
            user_id=current_user.id,
            filename=filename,  # важливо: зберігаємо лише назву
            created_at=now
        )
        db.session.add(new_report)
        db.session.commit()

        # === Оновлення usage.json ===
        if not is_pro:
            user_record["reports"] += 1
            usage_data[user_email] = user_record
            with open(usage_path, "w") as f:
                json.dump(usage_data, f, indent=2)

        # === Відправка файлу користувачу ===
        return send_file(os.path.abspath(pdf_path), as_attachment=True)

    except Exception as e:
        print("Error:", e)
        return f"Error: {e}", 500


@app.route('/report-history')
@login_required
def report_history():
    reports = Report.query.filter_by(user_id=current_user.id).order_by(Report.created_at.desc()).all()
    return render_template('report_history.html', reports=reports)


@app.route("/dashboard")
@login_required
def dashboard():
    user_email = current_user.email
    is_pro = user_email.endswith("@pro.com")

    # --- Зчитуємо usage.json ---
    usage_path = "usage.json"
    if os.path.exists(usage_path):
        with open(usage_path, "r") as f:
            usage_data = json.load(f)
    else:
        usage_data = {}

    now = datetime.now()
    user_record = usage_data.get(user_email, {"reports": 0, "last_reset": now.isoformat()})
    if now - datetime.fromisoformat(user_record["last_reset"]) > timedelta(days=14):
        user_record = {"reports": 0, "last_reset": now.isoformat()}

    remaining_reports = "Unlimited" if is_pro else max(0, 3 - user_record["reports"])

    return render_template("dashboard.html", is_pro=is_pro, remaining_reports=remaining_reports)


if __name__ == "__main__":
    app.run(debug=True)
