#!/bin/bash
# deploy.sh — Deploy to Google Cloud Run
# Usage: ./deploy.sh YOUR_PROJECT_ID [REGION]

PROJECT_ID=${1:-$(gcloud config get-value project)}
REGION=${2:-us-central1}
SERVICE_NAME="amazon-ads-analyzer"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "🚀 Deploying $SERVICE_NAME to Cloud Run..."
echo "   Project: $PROJECT_ID"
echo "   Region:  $REGION"
echo ""

# Build & push
echo "📦 Building Docker image..."
gcloud builds submit --tag $IMAGE

# Deploy
echo "☁️  Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --port 8080 \
  --max-instances 5

echo ""
echo "✅ Done! Your app URL:"
gcloud run services describe $SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --format "value(status.url)"
