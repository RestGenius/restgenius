from flask import Flask, request, render_template, send_file
import openai
import pdfkit
import os
import csv
import io
from datetime import datetime

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/", methods=["GET", "POST"])
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

    try:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        rows = list(csv_input)

        # Створюємо текст з CSV-даних
        sales_data = "\n".join([", ".join(row) for row in rows])

        # Формуємо запит до OpenAI
        prompt = f"""Here are the sales data:
{sales_data}

Generate detailed, actionable marketing suggestions to help this restaurant increase revenue."""

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )


        result = response.choices[0].message.content.strip()

        # Генеруємо PDF
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        html = render_template("report.html", content=result)
        pdf_path = f"report_{now}.pdf"
        pdfkit.from_string(html, pdf_path)

        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        print("Error parsing or analyzing CSV:", e)
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
    app.run(debug=True)
