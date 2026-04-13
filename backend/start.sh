#!/bin/bash
export GOOGLE_CLIENT_ID=test-google-client-id
export FACEBOOK_APP_ID=test-facebook-app-id
export FACEBOOK_APP_SECRET=test-facebook-app-secret
export SECRET_KEY=your-secret-key-for-jwt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload