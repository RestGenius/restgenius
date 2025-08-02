
from flask import Flask, request, render_template, send_file
import openai
import pdfkit
import os
from datetime import datetime

app = Flask(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/", methods=["GET", "POST"])
def index():
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
    
