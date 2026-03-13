import base64
import html
import json
import os
import requests
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# -------- ORG / APP SETTINGS --------
LOGIN_URL = os.getenv("SF_LOGIN_URL", "https://login.salesforce.com")
API_VERSION = os.getenv("SF_API_VERSION", "v66.0")
CLIENT_ID = os.getenv("SF_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SF_CLIENT_SECRET", "")
USERNAME = os.getenv("SF_USERNAME", "")
PASSWORD = os.getenv("SF_PASSWORD", "")

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
        .error { color: #e74c3c; font-weight: bold; background-color: #fadbd8; padding: 10px; border-radius: 5px; display: none;}
        .result { font-size: 1.1em; color: #2c3e50; display: none; }
        .debug-data { font-size: 0.85em; color: #7f8c8d; background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin-top: 20px; word-wrap: break-word; }
        ul { list-style-type: none; padding: 0; }
        li { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #eee; }
        strong { color: #34495e; }
        
        /* Spinner CSS */
        #loading-container { display: none; margin-top: 15px; }
        .loader {
            border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%;
            width: 25px; height: 25px; animation: spin 1s linear infinite; display: inline-block; vertical-align: middle;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        #status-text { display: inline-block; vertical-align: middle; margin-left: 10px; color: #555; font-weight: bold; }
    </style>
</head>
<body>
    <h2>Upload Any Document (Dynamic Extraction)</h2>
    <p>This app avoids Heroku timeouts by splitting the AI tasks into two steps.</p>
    
    <div class="box">
        <form id="uploadForm">
            <label for="document">Select a Document (PDF, JPG, PNG):</label><br><br>
            <input type="file" name="document" id="document" accept="application/pdf, image/jpeg, image/png" required><br><br>
            <button type="submit" id="submitBtn" style="padding: 10px 15px; cursor: pointer;">Upload & Dynamically Extract</button>
            
            <div id="loading-container">
                <div class="loader"></div>
                <div id="status-text">Starting...</div>
            </div>
        </form>
    </div>

    <div class="error" id="error-box"></div>

    <div class="box result" id="result-box">
        <h3>Extraction Results:</h3>
        <ul id="result-list"></ul>
        
        <div class="debug-data">
            <p><strong>Generated Schema (Debug):</strong></p>
            <pre id="debug-schema"></pre>
        </div>
    </div>

    <script>
        document.getElementById('uploadForm').addEventListener('submit', async function(e) {
            e.preventDefault(); // Prevent standard form submission
            
            const fileInput = document.getElementById('document');
            const file = fileInput.files[0];
            if (!file) return;

            // UI Updates
            document.getElementById('submitBtn').style.display = 'none';
            document.getElementById('error-box').style.display = 'none';
            document.getElementById('result-box').style.display = 'none';
            document.getElementById('loading-container').style.display = 'block';
            
            try {
                // STEP 1: Generate Schema
                document.getElementById('status-text').innerText = "Step 1/2: AI is generating a schema (approx 15s)...";
                let formData1 = new FormData();
                formData1.append('document', file);
                
                let res1 = await fetch('/generate', { method: 'POST', body: formData1 });
                let data1 = await res1.json();
                if (data1.error) throw new Error(data1.error);

                // STEP 2: Extract Data
                document.getElementById('status-text').innerText = "Step 2/2: Extracting data using new schema (approx 15s)...";
                let formData2 = new FormData();
                formData2.append('document', file);
                formData2.append('schema', data1.schema);

                let res2 = await fetch('/extract', { method: 'POST', body: formData2 });
                let data2 = await res2.json();
                if (data2.error) throw new Error(data2.error);

                // UI Updates: Success
                document.getElementById('loading-container').style.display = 'none';
                document.getElementById('result-box').style.display = 'block';
                
                // Populate List
                const ul = document.getElementById('result-list');
                ul.innerHTML = '';
                for (const [key, value] of Object.entries(data2.data)) {
                    // Format key nicely (e.g., first_name -> First Name)
                    let niceKey = key.replace(/_/g, ' ').replace(/\\b\\w/g, l => l.toUpperCase());
                    let li = document.createElement('li');
                    li.innerHTML = `<strong>${niceKey}:</strong> <br>${value}`;
                    ul.appendChild(li);
                }
                
                // Populate Debug Schema
                document.getElementById('debug-schema').innerText = data1.schema;

            } catch (err) {
                // UI Updates: Error
                document.getElementById('loading-container').style.display = 'none';
                const errorBox = document.getElementById('error-box');
                errorBox.style.display = 'block';
                errorBox.innerText = "Error: " + err.message;
            } finally {
                document.getElementById('submitBtn').style.display = 'inline-block';
            }
        });
    </script>
</body>
</html>
"""

# -------- Helpers --------
def headers_json(tok: str):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

def ssot_base(inst: str) -> str:
    return f"{inst}/services/data/{API_VERSION}/ssot/document-processing"

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

# -------- Web Routes --------
@app.route("/", methods=["GET"])
def index():
    # Just serve the frontend UI
    return render_template_string(HTML_TEMPLATE)

@app.route("/generate", methods=["POST"])
def api_generate():
    # API Endpoint for Step 1
    try:
        file = request.files["document"]
        file_bytes = file.read()
        file_b64 = base64.b64encode(file_bytes).decode("utf-8")
        
        token, instance_url = oauth_login()
        url = f"{ssot_base(instance_url)}/actions/generate-schema"
        payload = {
            "mlModel": ML_MODEL,
            "files": [{"data": file_b64, "mimeType": file.mimetype}]
        }
        r = requests.post(url, headers=headers_json(token), data=json.dumps(payload))
        
        if r.status_code in (200, 201):
            schema_str = r.json().get("schema")
            if schema_str:
                return jsonify({"schema": html.unescape(schema_str)})
        return jsonify({"error": f"Schema generation failed. Status: {r.status_code} Response: {r.text}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/extract", methods=["POST"])
def api_extract():
    # API Endpoint for Step 2
    try:
        file = request.files["document"]
        schema_str = request.form["schema"]
        file_bytes = file.read()
        file_b64 = base64.b64encode(file_bytes).decode("utf-8")
        
        token, instance_url = oauth_login()
        url = f"{ssot_base(instance_url)}/actions/extract-data"
        payload = {
            "schemaConfig": schema_str,
            "mlModel": ML_MODEL,
            "files": [{"data": file_b64, "mimeType": file.mimetype}]
        }
        r = requests.post(url, headers=headers_json(token), data=json.dumps(payload))
        
        if r.status_code in (200, 201):
            body = r.json()
            data_list = body.get("data")
            flat = {}
            if isinstance(data_list, list) and data_list:
                inner_str = data_list[0].get("data")
                if inner_str:
                    inner = json.loads(html.unescape(inner_str))
                    for k, v in inner.items():
                        flat[k] = v.get("value") if isinstance(v, dict) and "value" in v else v
            return jsonify({"data": flat})
            
        return jsonify({"error": f"Extraction failed. Status: {r.status_code} Response: {r.text}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
