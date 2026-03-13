import base64
import html
import json
import os
import requests
from flask import Flask, request, render_template_string

app = Flask(__name__)

# -------- ORG / APP SETTINGS --------
LOGIN_URL = os.getenv("SF_LOGIN_URL", "https://login.salesforce.com")
API_VERSION = os.getenv("SF_API_VERSION", "v66.0")
CLIENT_ID = os.getenv("SF_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SF_CLIENT_SECRET", "")
USERNAME = os.getenv("SF_USERNAME", "")
PASSWORD = os.getenv("SF_PASSWORD", "")

# The model used for both generating the schema and extracting the data
ML_MODEL = os.getenv("ML_MODEL", "llmgateway__OpenAIGPT4Omni_08_06")

# -------- INLINE HTML TEMPLATE --------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Dynamic Document AI Extractor</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; max-width: 700px; }
        .box { border: 1px solid #ccc; padding: 20px; border-radius: 5px; margin-top: 20px; }
        .error { color: #e74c3c; font-weight: bold; background-color: #fadbd8; padding: 10px; border-radius: 5px;}
        .result { font-size: 1.1em; color: #2c3e50; }
        .debug-data { font-size: 0.85em; color: #7f8c8d; background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin-top: 20px; word-wrap: break-word; }
        ul { list-style-type: none; padding: 0; }
        li { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #eee; }
        strong { color: #34495e; }
    </style>
</head>
<body>
    <h2>Upload Any Document (Dynamic Extraction)</h2>
    <p>This app will first ask AI to generate a schema for your specific document, and then extract the data based on that custom schema.</p>
    
    <div class="box">
        <form method="POST" enctype="multipart/form-data">
            <label for="document">Select a Document (PDF, JPG, PNG):</label><br><br>
            <input type="file" name="document" id="document" accept="application/pdf, image/jpeg, image/png" required><br><br>
            <button type="submit" style="padding: 10px 15px; cursor: pointer;">Upload & Dynamically Extract</button>
        </form>
    </div>

    {% if error %}
        <div class="error">
            <p>Error: {{ error }}</p>
        </div>
    {% endif %}

    {% if data %}
        <div class="box result">
            <h3>Extraction Results:</h3>
            <ul>
                {% for key, value in data.items() %}
                    <li><strong>{{ key | replace('_', ' ') | title }}:</strong> <br>{{ value }}</li>
                {% endfor %}
            </ul>
            
            <div class="debug-data">
                <p><strong>Generated Schema (Debug):</strong></p>
                <pre>{{ schema }}</pre>
            </div>
        </div>
    {% endif %}
</body>
</html>
"""

# -------- Helpers --------
def headers_json(tok: str):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

def ssot_base(inst: str) -> str:
    return f"{inst}/services/data/{API_VERSION}/ssot/document-processing"

# -------- Auth --------
def oauth_login():
    token_url = f"{LOGIN_URL}/services/oauth2/token"
    data = {
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": USERNAME,
        "password": PASSWORD,
    }
    r = requests.post(token_url, data=data)
    if r.status_code != 200:
        raise Exception(f"Login failed: {r.text}")
    j = r.json()
    return j.get("access_token"), j.get("instance_url")

# -------- Step 1: Generate Schema --------
def generate_schema(instance_url: str, token: str, file_b64: str, mime_type: str) -> str:
    url = f"{ssot_base(instance_url)}/actions/generate-schema"
    payload = {
        "mlModel": ML_MODEL,
        "files": [{"data": file_b64, "mimeType": mime_type}]
    }
    r = requests.post(url, headers=headers_json(token), data=json.dumps(payload))
    if r.status_code in (200, 201):
        schema_str = r.json().get("schema")
        if schema_str:
            # THE FIX: Unescape the HTML formatting (turn &quot; back into ")
            return html.unescape(schema_str)
    raise Exception(f"Failed to generate schema. Status: {r.status_code}. Response: {r.text}")

# -------- Step 2: Extract Data --------
def extract_data(instance_url: str, token: str, file_b64: str, mime_type: str, schema_str: str) -> dict:
    url = f"{ssot_base(instance_url)}/actions/extract-data"
    payload = {
        "schemaConfig": schema_str,
        "mlModel": ML_MODEL,
        "files": [{"data": file_b64, "mimeType": mime_type}]
    }
    r = requests.post(url, headers=headers_json(token), data=json.dumps(payload))
    if r.status_code in (200, 201):
        return r.json()
    raise Exception(f"Extraction failed. Status: {r.status_code}. Response: {r.text}")

def parse_extracted_values(extract_body: dict) -> dict:
    data_list = extract_body.get("data")
    if isinstance(data_list, list) and data_list:
        inner_str = data_list[0].get("data")
        if inner_str:
            inner = json.loads(html.unescape(inner_str))
            if isinstance(inner, dict):
                flat = {}
                for k, v in inner.items():
                    if isinstance(v, dict) and "value" in v:
                        flat[k] = v.get("value")
                    else:
                        flat[k] = v
                return flat
    return {}

# -------- Web Routes --------
@app.route("/", methods=["GET", "POST"])
def index():
    extracted_data = None
    generated_schema = None
    error_message = None

    if request.method == "POST":
        if "document" not in request.files:
            error_message = "No file uploaded."
        else:
            file = request.files["document"]
            if file.filename == "":
                error_message = "No file selected."
            else:
                try:
                    file_bytes = file.read()
                    file_b64 = base64.b64encode(file_bytes).decode("utf-8")
                    mime_type = file.mimetype

                    token, instance_url = oauth_login()
                    
                    # 1. Ask Salesforce to generate a dynamic schema
                    generated_schema = generate_schema(instance_url, token, file_b64, mime_type)
                    
                    # 2. Pass that clean schema back to extract the actual data
                    raw_extraction = extract_data(instance_url, token, file_b64, mime_type, generated_schema)
                    
                    # 3. Parse and display
                    extracted_data = parse_extracted_values(raw_extraction)

                except Exception as e:
                    error_message = str(e)

    return render_template_string(HTML_TEMPLATE, data=extracted_data, schema=generated_schema, error=error_message)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
