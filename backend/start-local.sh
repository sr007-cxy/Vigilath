#!/bin/bash
# Disable dotenv prompt
export DOTENV_SKIP_LOAD=true

# Set environment variables
export GOOGLE_CLIENT_ID=test-google-client-id
export SECRET_KEY=your-secret-key-for-jwt

# Start the service
# GEO_NO_CACHE=1
./venv/bin/python -m uvicorn geo.main:app --host 0.0.0.0 --port 8070 --reload
