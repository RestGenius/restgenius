from flask import (
    Flask, request, render_template, send_file, url_for,
    redirect, send_from_directory, abort
)
from flask_login import (
    LoginManager, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import safe_join
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from openai import OpenAI
from sqlalchemy import text
import pdfkit
import os
import csv
import io
from datetime import datetime, timedelta

from models import db, User, Report

# === APP CONFIG ===
app = Flask(__name__)
app.config.update(
    MAIL_SERVER="smtp.gmail.com",
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.environ.get("EMAIL_USER"),
    MAIL_PASSWORD=(os.environ.get("EMAIL_PASS") or os.environ.get("EMAIL_PASSWORD")),
    MAIL_DEFAULT_SENDER=(os.environ.get("MAIL_DEFAULT_SENDER") or os.environ.get("EMAIL_USER")),
    SECRET_KEY=os.environ.get("SECRET_KEY", "mysecret"),
    SQLALCHEMY_DATABASE_URI="sqlite:///users.db",
)

# === MAIL CHECK ===
MAIL_ENABLED = all((
    app.config.get("MAIL_USERNAME"),
    app.config.get("MAIL_PASSWORD"),
    app.config.get("MAIL_DEFAULT_SENDER"),
))
AUTO_VERIFY_IF_NO_MAIL = os.getenv("AUTO_VERIFY_IF_NO_MAIL", "1") == "1"
if MAIL_ENABLED:
    app.logger.warning("[MAIL DIAG] USER:True PASS:True SENDER:True")
else:
    app.logger.warning("[MAIL DIAG] Mail is NOT fully configured. Email sending DISABLED.")

# === INIT ===
mail = Mail(app)
db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

with app.app_context():
    db.create_all()

# === OpenAI v1 client (safe logging) ===
raw_key = os.getenv("OPENAI_API_KEY", "")
masked = (raw_key[:7] + "..." + raw_key[-4:]) if raw_key and len(raw_key) > 11 else ("MISSING" if not raw_key else raw_key[:7] + "...")
app.logger.info(f"[OpenAI] API key loaded? {'YES' if raw_key else 'NO'} ({masked})")
client = OpenAI(api_key=raw_key)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# === Helper: Check and Reset Limits ===
def check_and_reset_limits(user: User):
    """
    Скидає лічильники безкоштовних звітів кожні 14 днів.
    """
    now = datetime.utcnow()
    if not user.free_reports_reset or (now - user.free_reports_reset) > timedelta(days=14):
        user.free_reports_used = 0
        user.free_reports_reset = now
        db.session.commit()

def _allowed_csv(filename: str) -> bool:
    return filename.lower().endswith(".csv")

# === ROUTES ===
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return "Email and password are required", 400

        if User.query.filter_by(email=email).first():
            return "User already exists"

        hashed_pw = generate_password_hash(password, method="pbkdf2:sha256")
        new_user = User(email=email, password=hashed_pw, is_verified=False)
        db.session.add(new_user)
        db.session.commit()

        if MAIL_ENABLED:
            s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
            token = s.dumps(email, salt="email-confirm")
            link = url_for('confirm_email', token=token, _external=True)

            msg = Message(
                "Confirm your email",
                sender=app.config["MAIL_DEFAULT_SENDER"],
                recipients=[email],
            )
            msg.html = f"""
            <h3>Welcome to RestGenius!</h3>
            <p>Click the button below to verify your email:</p>
            <a href="{link}" style="padding: 10px 20px; background: #1a73e8; color: white; text-decoration: none; border-radius: 6px;">✅ Confirm Email</a>
            """
            try:
                mail.send(msg)
                return "✅ Registration successful. Please check your email to confirm."
            except Exception:
                app.logger.exception("Mail send failed")
                return "We couldn't send the confirmation email right now. Please try again later.", 200
        else:
            if AUTO_VERIFY_IF_NO_MAIL:
                new_user.is_verified = True
                db.session.commit()
                return "✅ Registration successful (dev mode). Email auto-verified. You can log in now."
            else:
                return "✅ Registration successful. Email verification is disabled.", 200

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
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            return "Invalid credentials"
        if not user.is_verified:
            return "❗ Please verify your email before logging in."
        login_user(user)
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return "Logged out successfully"

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    # Перевірка верифікації
    if not current_user.is_verified:
        return "❌ Please verify your email before using this feature.", 403

    # Скидання/перевірка лімітів
    check_and_reset_limits(current_user)

    # Ліміт для FREE (3 звіти / 14 днів)
    if not current_user.is_pro and (current_user.free_reports_used or 0) >= 3:
        return "<h2>❌ Free Limit Reached</h2><p>Please upgrade to PRO.</p>", 403

    if 'file' not in request.files:
        return "No file uploaded", 400

    file = request.files['file']
    if not file or file.filename == '':
        return "No file selected", 400
    if not _allowed_csv(file.filename):
        return "File must be a CSV", 400

    try:
        # Прочитали CSV
        stream = io.StringIO(file.stream.read().decode("utf-8"), newline=None)
        rows = list(csv.reader(stream))
        if not rows:
            return "CSV is empty", 400

        # (опційно) обмежити розмір для безпеки
        rows = rows[:5000]

        sales_data = "\n".join([", ".join(row) for row in rows])

        # Основний запит
        main_prompt = (
            "You are an expert restaurant consultant. "
            "Analyze the following sales data and provide clear, structured insights, "
            "quick wins, and concrete next actions:\n\n"
            f"{sales_data}"
        )
        chat_completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": main_prompt}],
            temperature=0.2,
        )
        result = chat_completion.choices[0].message.content.strip()

        roi_forecast, top_campaign = "", ""
        if current_user.is_pro:
            roi_prompt = (
                "Provide a brief ROI forecast (assumptions + numbers) based on this sales data:\n\n"
                f"{sales_data}"
            )
            campaign_prompt = (
                "Propose one high-impact marketing campaign tailored to this sales data. "
                "Include target segment, offer, channel, and 3 KPIs:\n\n"
                f"{sales_data}"
            )

            roi_forecast = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": roi_prompt}],
                temperature=0.2,
            ).choices[0].message.content.strip()

            top_campaign = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": campaign_prompt}],
                temperature=0.2,
            ).choices[0].message.content.strip()

        # Рендеримо HTML звіту
        html = render_template(
            "report.html",
            content=result,
            is_pro=current_user.is_pro,
            roi_forecast=roi_forecast,
            top_campaign=top_campaign
        )

        # Генеруємо PDF (або HTML fallback)
        reports_dir = "reports"
        os.makedirs(reports_dir, exist_ok=True)
        filename_base = f"report_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}"
        pdf_path = os.path.join(reports_dir, f"{filename_base}.pdf")

        try:
            pdfkit.from_string(html, pdf_path)
            file_to_send = os.path.abspath(pdf_path)
            stored_name = f"{filename_base}.pdf"
        except Exception:
            app.logger.exception("pdfkit failed; falling back to HTML")
            html_fallback = os.path.join(reports_dir, f"{filename_base}.html")
            with open(html_fallback, "w", encoding="utf-8") as f:
                f.write(html)
            file_to_send = os.path.abspath(html_fallback)
            stored_name = f"{filename_base}.html"

        # Зберігаємо запис про звіт
        new_report = Report(
            user_id=current_user.id,
            filename=stored_name,
            created_at=datetime.utcnow()
        )
        db.session.add(new_report)

        # Інкремент ліміту для FREE
        if not current_user.is_pro:
            current_user.free_reports_used = (current_user.free_reports_used or 0) + 1

        db.session.commit()

        # Віддаємо файл
        return send_file(file_to_send, as_attachment=True)

    except Exception:
        app.logger.exception("Analyze failed")
        # не віддаємо користувачу внутрішній стек/ключі
        return "Internal error while generating the report. Please try again.", 500

@app.route('/report-history')
@login_required
def report_history():
    reports = Report.query.filter_by(user_id=current_user.id)\
        .order_by(Report.created_at.desc()).all()
    return render_template('report_history.html', reports=reports)

@app.route("/download-report/<path:filename>")
@login_required
def download_report(filename):
    # Перевіряємо, що файл належить користувачу
    report = Report.query.filter_by(user_id=current_user.id, filename=filename).first()
    if not report:
        abort(404)

    # Перевірка шляху
    reports_dir = os.path.abspath("reports")
    safe_path = safe_join(reports_dir, filename)
    if not safe_path or not os.path.isfile(safe_path):
        abort(404)

    return send_from_directory(reports_dir, filename, as_attachment=True)

@app.route("/dashboard")
@login_required
def dashboard():
    check_and_reset_limits(current_user)
    remaining_reports = "Unlimited" if current_user.is_pro else max(0, 3 - (current_user.free_reports_used or 0))
    return render_template("dashboard.html", is_pro=current_user.is_pro, remaining_reports=remaining_reports)

@app.route("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
        return "ok", 200
    except Exception as e:
        return f"db error: {e}", 500

@app.route("/healthz/openai")
def healthz_openai():
    """
    Діагностичний пінг до OpenAI. Може згенерувати мінімальні витрати.
    Видали в проді, якщо не потрібен.
    """
    try:
        # дуже короткий виклик
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0
        )
        return "ok", 200
    except Exception as e:
        app.logger.exception("OpenAI health failed")
        return f"openai error: {e}", 500

if __name__ == "__main__":
    # debug=True не бажано в проді, але лишаємо для локального запуску
    app.run(debug=True)
