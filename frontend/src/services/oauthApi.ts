

class OAuthApi {
  private baseUrl: string;

  constructor() {
    this.baseUrl = 'http://localhost:8000/api/oauth';
  }

  async googleLogin(googleToken: string) {
    const response = await fetch(`${this.baseUrl}/google`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ google_token: googleToken }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to login with Google');
    }

    return response.json();
  }

  async facebookLogin(facebookToken: string) {
    const response = await fetch(`${this.baseUrl}/facebook`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ facebook_token: facebookToken }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to login with Facebook');
    }

    return response.json();
  }
}

export const oauthApi = new OAuthApi();