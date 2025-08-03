
from flask import Flask, request, render_template, send_file
import openai
import pdfkit
import os
from datetime import datetime

app = Flask(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/", methods=["GET", "POST"])
def index():
    from flask import request, redirect, flash
import csv
import io

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

        # Тестовий вивід в лог
        for row in rows:
            print(row)

        return "File uploaded and parsed successfully!"
    except Exception as e:
        print("Error parsing CSV:", e)
        return f"Error parsing file: {str(e)}", 500

    if request.method == "POST":
        sales_data = request.form["sales_data"]
        prompt = f"""Ось дані про продажі:
{sales_data}

Згенеруй ідеї для маркетингових акцій, які допоможуть збільшити прибуток ресторану."""

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )

        result = response.choices[0].message.content.strip()

        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        html = render_template("report.html", content=result)
        pdf_path = f"report_{now}.pdf"
        pdfkit.from_string(html, pdf_path)

        return send_file(pdf_path, as_attachment=True)

    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)
    
