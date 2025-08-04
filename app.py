from flask import Flask, request, render_template, send_file
import openai
import pdfkit
import os
import csv
import io
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User

app = Flask(__name__)

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    import json
    from datetime import timedelta

    # --- Перевірка файлу ---
    if 'file' not in request.files:
        return "No file uploaded", 400

    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400

    if not file.filename.endswith('.csv'):
        return "File must be a CSV", 400

    # --- Отримуємо email ---
    user_email = request.form.get('email', '').strip().lower()
    if not user_email:
        return "Email is required", 400

    # --- Перевірка PRO ---
    def check_if_user_is_pro(email):
        return email.endswith('@pro.com')

    is_pro = check_if_user_is_pro(user_email)

    @app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        hashed_pw = generate_password_hash(password, method="sha256")

        if User.query.filter_by(email=email).first():
            return "User already exists"

        new_user = User(email=email, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()

        return "Registration successful. Please log in."

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            return "Invalid credentials"

        login_user(user)
        return "Login successful. Go to /dashboard"

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return "Logged out successfully"


@app.route("/dashboard")
@login_required
def dashboard():
    return f"Welcome, {current_user.email}! [PRO: {current_user.is_pro}]"


    # --- Читаємо usage.json ---
    usage_path = "usage.json"
    if os.path.exists(usage_path):
        with open(usage_path, "r") as f:
            usage_data = json.load(f)
    else:
        usage_data = {}

    now = datetime.now()
    user_record = usage_data.get(user_email, {
        "reports": 0,
        "last_reset": now.isoformat()
    })

    # --- Скидаємо лічильник, якщо пройшло >14 днів ---
    last_reset = datetime.fromisoformat(user_record["last_reset"])
    if now - last_reset > timedelta(days=14):
        user_record["reports"] = 0
        user_record["last_reset"] = now.isoformat()

    # --- Перевірка ліміту ---
    if not is_pro and user_record["reports"] >= 3:
        return """
        <h2>❌ Free Limit Reached</h2>
        <p>You have used all 3 reports for this 2-week period.</p>
        <p>Upgrade to PRO to unlock unlimited reports and advanced features.</p>
        """, 403

    try:
        # --- Обробка CSV ---
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        rows = list(csv_input)
        sales_data = "\n".join([", ".join(row) for row in rows])

        # --- Промпти ---
        roi_prompt = f"""You're an expert in restaurant finance...{sales_data}"""
        campaign_prompt = f"""You're an AI restaurant strategist...{sales_data}"""
        main_prompt = f"""
You're an expert restaurant marketing consultant. Analyze the following sales data:

{sales_data}

Generate a professional, well-structured growth report including:
- Key recommendations
- Action steps
- Data-backed justifications
{"- ROI projections\n- Financial forecast\n- Strategic insights" if is_pro else ""}
"""

        # --- Генерація GPT-відповіді ---
        chat_completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": main_prompt}]
        )
        result = chat_completion.choices[0].message.content.strip()

        # --- PRO-додатки ---
        roi_forecast, top_campaign = "", ""
        if is_pro:
            roi_response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": roi_prompt}]
            )
            roi_forecast = roi_response.choices[0].message.content.strip()

            campaign_response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": campaign_prompt}]
            )
            top_campaign = campaign_response.choices[0].message.content.strip()

        # --- Рендеринг PDF ---
        html = render_template("report.html",
                               content=result,
                               is_pro=is_pro,
                               roi_forecast=roi_forecast,
                               top_campaign=top_campaign)
        pdf_path = f"report_{now.strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
        pdfkit.from_string(html, pdf_path)

        # --- Оновлення usage.json ---
        if not is_pro:
            user_record["reports"] += 1
            usage_data[user_email] = user_record
            with open(usage_path, "w") as f:
                json.dump(usage_data, f, indent=2)

        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        print("Error:", e)
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
    app.run(debug=True)
    
# 🔧 Налаштування бази
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "mysecret")

# Ініціалізація
db.init_app(app)

with app.app_context():
    db.create_all()

# 🔐 Налаштування логіну
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
