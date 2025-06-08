from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import requests, json, base64, mimetypes, os, re
from datetime import timedelta
from dateutil import parser, tz
from flask_cors import CORS    # <- lets Streamlit hit the API from :8501

# -------------------------------------------------------------------
#  Config
# -------------------------------------------------------------------
GEMINI_KEY = "AIzaSyDs0HK8t9QOVytjx9G153QCIEXKdLWzA54"
MODEL      = "gemini-1.5-flash"
TIMEZONE   = "America/Los_Angeles"
SCOPES     = ["https://www.googleapis.com/auth/calendar"]

import os
import json
from google.oauth2.credentials import Credentials

token_json_str = os.getenv("TOKEN_JSON")
creds = Credentials.from_authorized_user_info(json.loads(token_json_str), SCOPES)


app = Flask(__name__)
CORS(app)                      # allow requests from the Streamlit front-end
# -------------------------------------------------------------------

def call_gemini(image_file):
    """Send image → Gemini Vision, return dict with title/date/time/…"""
    b64 = base64.b64encode(image_file.read()).decode("utf-8")
    mime = image_file.content_type or mimetypes.guess_type(image_file.filename)[0]
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": (
                    "Extract event details ONLY as valid JSON:\n"
                    '{"title":"","date":"","time":"","location":"","description":""}'
                )}
            ]
        }]
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_KEY}"
    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()

    txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    # Grab the first {...} block
    try:
        event_json = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception:
        raise ValueError(f"Gemini returned non-JSON: {txt}")

    return event_json


ordinal_pat = re.compile(r"(\d{1,2})(st|nd|rd|th)", re.I)
def clean_datetime(date_str: str, time_str: str):
    # strip st/nd/rd/th from day number
    date_str = ordinal_pat.sub(r"\1", date_str.strip())

    time_str = time_str.strip().lower()
    # remove am/pm if we already have 24-h clock
    if re.search(r"\b(am|pm)\b", time_str) and re.match(r"\d{2}:\d{2}", time_str):
        time_str = re.sub(r"\s*(am|pm)\b", "", time_str)

    dt = parser.parse(f"{date_str} {time_str}", dayfirst=False, fuzzy=True)
    return dt.replace(tzinfo=tz.gettz(TIMEZONE))


def to_rfc3339(date_str, time_str):
    start = clean_datetime(date_str, time_str)
    end   = start + timedelta(hours=1)
    return start.isoformat(), end.isoformat()


# --------------------------  ROUTES  --------------------------------
@app.route("/extract", methods=["POST"])
def extract_event():
    try:
        image = request.files["image"]
        data  = call_gemini(image)
        return jsonify(data)
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": str(e)}), 500


@app.route("/create", methods=["POST"])
def create_event():
    try:
        data = request.json or {}
        # provide sane defaults so parser won’t KeyError
        title = data.get("title", "Untitled event")
        date  = data.get("date")
        time  = data.get("time", "09:00")   # default 9 AM
        start, end = to_rfc3339(date, time)

        event = {
            "summary":     title,
            "location":    data.get("location"),
            "description": data.get("description"),
            "start": {"dateTime": start},
            "end":   {"dateTime": end},
        }
        service = build("calendar", "v3", credentials=creds)
        created = service.events().insert(calendarId="primary", body=event).execute()

        return jsonify({"message": "Event created",
                        "eventLink": created.get("htmlLink")})
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
