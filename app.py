from flask import (
    Flask, request, render_template, send_file, url_for,
    redirect, send_from_directory, abort, make_response
)
from flask_login import (
    LoginManager, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import safe_join
from werkzeug.exceptions import RequestEntityTooLarge
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

# Ліміт розміру аплоада (за замовчуванням 10 МБ, змінити через MAX_UPLOAD_MB)
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

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

# === Helpers ===
def check_and_reset_limits(user: User):
    """Скидає лічильники безкоштовних звітів кожні 14 днів."""
    now = datetime.utcnow()
    if not user.free_reports_reset or (now - user.free_reports_reset) > timedelta(days=14):
        user.free_reports_used = 0
        user.free_reports_reset = now
        db.session.commit()
        app.logger.info("[LIMIT] reset user_id=%s", user.id)

def _allowed_csv(filename: str) -> bool:
    return filename.lower().endswith(".csv")

def _toast_redirect(message_cookie: str = "rg_error"):
    """Редірект на дашборд з коротким toast-прапорцем у кукі."""
    resp = redirect(url_for("dashboard"))
    resp.set_cookie(message_cookie, "1", max_age=300, samesite="Lax")
    return resp

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
                app.logger.exception("[MAIL] send failed")
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

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    app.logger.warning("[UPLOAD] too_large user_id=%s max_mb=%s", getattr(current_user, "id", None), MAX_UPLOAD_MB)
    # 413 → редірект і toast
    return _toast_redirect("rg_err_size")

@app.route("/sample-csv")
@login_required
def sample_csv():
    """Віддає приклад CSV для швидкого тесту."""
    sample = io.StringIO()
    writer = csv.writer(sample)
    writer.writerow(["date", "item", "category", "qty", "price"])
    writer.writerow(["2025-08-01", "Margherita Pizza", "Food", "14", "9.90"])
    writer.writerow(["2025-08-01", "Cappuccino", "Beverage", "23", "3.50"])
    writer.writerow(["2025-08-02", "Caesar Salad", "Food", "9", "7.50"])
    writer.writerow(["2025-08-02", "Lemonade", "Beverage", "15", "2.80"])
    writer.writerow(["2025-08-03", "Tiramisu", "Dessert", "11", "4.20"])
    data = sample.getvalue().encode("utf-8")
    fname = "restgenius_sample.csv"
    resp = make_response(data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp

# === DEV Upgrade to PRO ===
@app.route("/upgrade/dev")
@login_required
def upgrade_dev():
    token = request.args.get("token", "")
    expected = os.getenv("DEV_UPGRADE_TOKEN", "")
    if not expected or token != expected:
        abort(403)

    current_user.is_pro = True
    db.session.commit()
    app.logger.info("[UPGRADE] dev_pro user_id=%s", current_user.id)

    resp = redirect(url_for("dashboard"))
    resp.set_cookie("rg_upgraded", "1", max_age=300, samesite="Lax")
    return resp

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    # Перевірка верифікації
    if not current_user.is_verified:
        return "❌ Please verify your email before using this feature.", 403

    # Скидання/перевірка лімітів
    check_and_reset_limits(current_user)

    # Ліміт для FREE (3 звіти / 14 днів) — тепер редірект з toast, а не 403-сторінка
    if not current_user.is_pro and (current_user.free_reports_used or 0) >= 3:
        return _toast_redirect("rg_err_limit")

    if 'file' not in request.files:
        return _toast_redirect("rg_err_no_file")

    file = request.files['file']
    if not file or file.filename == '':
        return _toast_redirect("rg_err_no_file")
    if not _allowed_csv(file.filename):
        return _toast_redirect("rg_err_type")

    if not raw_key:
        app.logger.error("[OPENAI] missing_api_key user_id=%s", current_user.id)
        return _toast_redirect("rg_err_auth")

    try:
        # Прочитали CSV
        stream = io.StringIO(file.stream.read().decode("utf-8"), newline=None)
        rows = list(csv.reader(stream))
        if not rows:
            return _toast_redirect("rg_err_empty")

        # (опційно) обмежити розмір для безпеки
        rows = rows[:5000]

        sales_data = "\n".join([", ".join(row) for row in rows])

        # === Промпти з вимогою HTML-фрагментів ===
        main_prompt = (
            "You are an expert restaurant consultant. "
            "Using the SALES CSV below, return a CLEAN HTML FRAGMENT (no <html> or <body>) "
            "with these sections using <h2>, <p>, and <ul><li>: "
            "1) Executive Summary, 2) Key Insights, 3) Quick Wins, 4) Next Actions. "
            "Do not use emojis. Keep it concise and scannable. English only.\n\n"
            "SALES CSV:\n"
            f"{sales_data}"
        )

        chat_completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": main_prompt}],
            temperature=0.2,
        )
        result_html = chat_completion.choices[0].message.content.strip()

        roi_html, campaign_html = "", ""
        if current_user.is_pro:
            roi_prompt = (
                "Return a CLEAN HTML FRAGMENT with <h2>ROI Forecast</h2> followed by a short "
                "<p>assumptions</p> and a <ul><li>list of numeric estimates</li></ul> "
                "based on this SALES CSV. No outer <html>/<body>. English.\n\n"
                f"{sales_data}"
            )
            campaign_prompt = (
                "Return a CLEAN HTML FRAGMENT with <h2>Recommended Campaign</h2> and a brief "
                "<p>why it fits</p> plus a <ul><li>Target</li><li>Offer</li><li>Channel</li><li>3 KPIs</li></ul>. "
                "No outer <html>/<body>. English.\n\n"
                f"{sales_data}"
            )

            roi_html = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": roi_prompt}],
                temperature=0.2,
            ).choices[0].message.content.strip()

            campaign_html = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": campaign_prompt}],
                temperature=0.2,
            ).choices[0].message.content.strip()

        # Рендеримо HTML звіту
        html = render_template(
            "report.html",
            content=result_html,
            is_pro=current_user.is_pro,
            roi_forecast=roi_html,
            top_campaign=campaign_html
        )

        # Генеруємо PDF (або HTML fallback) з опціями
        reports_dir = "reports"
        os.makedirs(reports_dir, exist_ok=True)
        filename_base = f"report_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}"
        pdf_path = os.path.join(reports_dir, f"{filename_base}.pdf")

        options = {
            "encoding": "UTF-8",
            "page-size": "A4",
            "margin-top": "10mm",
            "margin-right": "10mm",
            "margin-bottom": "12mm",
            "margin-left": "10mm",
            "quiet": None,
        }

        try:
            pdfkit.from_string(html, pdf_path, options=options)
            file_to_send = os.path.abspath(pdf_path)
            stored_name = f"{filename_base}.pdf"
        except Exception as pdf_err:
            app.logger.exception("[PDFKIT] failed; fallback to HTML. Hint: ensure wkhtmltopdf is installed on host. err=%s", pdf_err)
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
        app.logger.info("[REPORT] generated user_id=%s file=%s", current_user.id, stored_name)

        # Віддаємо файл + ставимо кукі для toast на дашборді
        response = send_file(file_to_send, as_attachment=True)
        response.set_cookie("rg_generated", "1", max_age=300, samesite="Lax")
        return response

    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "invalid api key" in msg:
            app.logger.error("[OPENAI] auth_error user_id=%s err=%s", current_user.id, e)
            return _toast_redirect("rg_err_auth")
        if "429" in msg or "rate limit" in msg:
            app.logger.error("[OPENAI] rate_limited user_id=%s err=%s", current_user.id, e)
            return _toast_redirect("rg_err_rate")
        if "timeout" in msg:
            app.logger.error("[OPENAI] timeout user_id=%s err=%s", current_user.id, e)
            return _toast_redirect("rg_err_timeout")

        app.logger.exception("[ANALYZE] failed user_id=%s", current_user.id)
        return _toast_redirect("rg_error")

@app.route('/report-history')
@login_required
def report_history():
    # Пагінація: 20/стор.
    page = max(1, int(request.args.get("page", 1)))
    per_page = 20
    q = Report.query.filter_by(user_id=current_user.id)
    total = q.count()
    reports = q.order_by(Report.created_at.desc())\
               .offset((page - 1) * per_page)\
               .limit(per_page).all()

    has_prev = page > 1
    has_next = (page * per_page) < total
    return render_template(
        'report_history.html',
        reports=reports, page=page, per_page=per_page, total=total,
        has_prev=has_prev, has_next=has_next
    )

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

@app.route("/preview-report/<path:filename>")
@login_required
def preview_report(filename):
    """Віддає звіт inline для вбудованого перегляду (iframe)."""
    report = Report.query.filter_by(user_id=current_user.id, filename=filename).first()
    if not report:
        abort(404)

    reports_dir = os.path.abspath("reports")
    safe_path = safe_join(reports_dir, filename)
    if not safe_path or not os.path.isfile(safe_path):
        abort(404)

    return send_from_directory(reports_dir, filename, as_attachment=False)

@app.route("/dashboard")
@login_required
def dashboard():
    check_and_reset_limits(current_user)
    is_pro = bool(current_user.is_pro)
    remaining_reports = "Unlimited" if is_pro else max(0, 3 - (current_user.free_reports_used or 0))

    # countdown до наступного ресету
    reset_days = reset_hours = next_reset_iso = None
    if not is_pro:
        anchor = current_user.free_reports_reset or datetime.utcnow()
        next_reset = anchor + timedelta(days=14)
        delta = max(timedelta(0), next_reset - datetime.utcnow())
        reset_days = delta.days
        reset_hours = (delta.seconds // 3600)
        next_reset_iso = next_reset.isoformat()

    last_report = Report.query.filter_by(user_id=current_user.id)\
        .order_by(Report.created_at.desc()).first()
    dev_token = os.getenv("DEV_UPGRADE_TOKEN", "")

    return render_template(
        "dashboard.html",
        is_pro=is_pro,
        is_verified=bool(current_user.is_verified),
        remaining_reports=remaining_reports,
        reset_days=reset_days,
        reset_hours=reset_hours,
        next_reset_iso=next_reset_iso,
        last_report=last_report,
        max_upload_mb=MAX_UPLOAD_MB,
        dev_token=dev_token
    )

@app.route("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
        return "ok", 200
    except Exception as e:
        return f"db error: {e}", 500

@app.route("/healthz/openai")
def healthz_openai():
    try:
        client.chat.completions.create(
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
