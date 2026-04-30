# Amazon Ads Placement Analyzer

Streamlit web app for triple gifted advertising team.
Analyzes Amazon Sponsored Products + Sponsored Brands placement reports,
scores campaigns 1-100, generates bid recommendations, alerts, and AI comments via Claude API.

---

## Project Structure

```
amazon-ads-analyzer/
├── app.py              # Streamlit UI
├── analyzer.py         # Scoring + bid logic (deterministic, no API)
├── claude_client.py    # Claude API — generates comments only
├── excel_builder.py    # Builds the 3-sheet Excel output
├── requirements.txt
├── Dockerfile
└── deploy.sh           # One-command deploy to Google Cloud Run
```

---

## Local Development

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Deploy to Google Cloud Run

### Prerequisites
```bash
# Install Google Cloud CLI
brew install google-cloud-sdk   # Mac
# or: https://cloud.google.com/sdk/docs/install

# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### One-command deploy
```bash
chmod +x deploy.sh
./deploy.sh
```

### Manual deploy
```bash
# Build and push image
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/amazon-ads-analyzer

# Deploy to Cloud Run
gcloud run deploy amazon-ads-analyzer \
  --image gcr.io/YOUR_PROJECT_ID/amazon-ads-analyzer \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --port 8080
```

---

## How to Use

1. Open the app URL (from Cloud Run)
2. Add your Anthropic API key in the sidebar (optional — enables AI comments)
3. Adjust Target ROAS and alert threshold if needed (defaults: ROAS 3.0, impressions 1,000)
4. Upload both files:
   - **Sponsored Products Placement Report** (.xlsx) — 30 days
   - **Sponsored Brands Campaign Placement Report** (.xlsx) — 30 days
5. Click **Run Analysis**
6. Review scores, alerts, and campaign table
7. Download the Excel report

---

## Scoring Logic (1–100)

| Component | Weight |
|---|---|
| Overall ROAS | 35 pts |
| Top of Search ROAS | 35 pts |
| Top vs Rest advantage | 15 pts |
| CTR at Top | 10 pts |
| Orders volume at Top | 5 pts |

Score labels:
- **80–100** → Invest aggressively
- **60–79** → Worth it, scale gradually
- **40–59** → Test before scaling
- **< 40** → Not recommended now

---

## Bid Recommendation Logic

Every placement evaluated independently against Target ROAS:
- ROAS above target → recommend % increase (calculated from ratio vs average)
- ROAS below target → 0%, do not raise

Example output: `Top: +57% | Rest: +10% | Product: 0% (ROAS 2.2 < target)`

---

## Alert Logic

Triggered when: score ≥ 1 AND Top of Search impressions < threshold (default 1,000/30 days)
Meaning: campaign has good ROAS at Top but Amazon barely shows it there → raise bid

---

## Modifying the Logic

All scoring and bid logic is in `analyzer.py` — no API calls, pure Python.
Claude API is only called in `claude_client.py` for generating text comments.
To change scoring weights, edit the `score_campaign()` function.
To change bid % formula, edit `bid_recommendation()`.
