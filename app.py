from flask import Flask, request, render_template, send_file
import openai
import pdfkit
import os
import csv
import io
from datetime import datetime

app = Flask(__name__)

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    if 'file' not in request.files:
        return "No file uploaded", 400

    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400

    if not file.filename.endswith('.csv'):
        return "File must be a CSV", 400

    # 👉 Отримуємо email із форми
    user_email = request.form.get('email', '')

    # ✅ Перевірка PRO
    def check_if_user_is_pro(email):
        return email.endswith('@pro.com')

    is_pro = check_if_user_is_pro(user_email)

    try:
        # 🔄 Читання CSV
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        rows = list(csv_input)
        sales_data = "\n".join([", ".join(row) for row in rows])

        # 🔮 Промпти
        prompt = f"""
You're an expert restaurant marketing consultant. Analyze the following sales data:

{sales_data}

Generate a professional, well-structured growth report including:
- Key recommendations
- Action steps
- Data-backed justifications
{"- ROI projections\n- Financial forecast\n- Strategic insights" if is_pro else ""}
"""

        roi_prompt = f"""
You're an expert in restaurant finance and business growth. Based on the following sales data:

{sales_data}

Generate a concise but clear ROI & financial forecast for implementing smart marketing strategies.
Include:
- Expected revenue uplift (in % and $)
- Changes in average order value
- Operational efficiency improvements
- ROI ratio (approximate)
Present the content in professional, structured English.
"""

        campaign_prompt = f"""
You're an AI restaurant marketing strategist. Based on this sales data:

{sales_data}

Suggest the most effective, high-ROI marketing campaign idea for the restaurant.
Keep it under 20 words. Return only the campaign title.
"""

        # 🧠 Генерація основного звіту
        chat_completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        result = chat_completion.choices[0].message.content.strip()

        # 🧠 PRO-блоки, тільки якщо is_pro
        roi_forecast = ""
        top_campaign = ""
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

        # 🧾 Генерація PDF
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        html = render_template("report.html", 
                               content=result,
                               is_pro=is_pro,
                               roi_forecast=roi_forecast,
                               top_campaign=top_campaign)

        pdf_path = f"report_{now}.pdf"
        pdfkit.from_string(html, pdf_path)
        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        print("Error:", e)
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
    app.run(debug=True)
