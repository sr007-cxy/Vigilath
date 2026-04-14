#!/bin/bash
# 禁用 dotenv 提示
export DOTENV_SKIP_LOAD=true

# 设置环境变量
export GOOGLE_CLIENT_ID=test-google-client-id
export FACEBOOK_APP_ID=test-facebook-app-id
export FACEBOOK_APP_SECRET=test-facebook-app-secret
export SECRET_KEY=your-secret-key-for-jwt

# 启动服务
python3 -m uvicorn geo.main:app --host 0.0.0.0 --port 8070 --reload