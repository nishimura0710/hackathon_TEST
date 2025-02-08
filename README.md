# Google Calendar Bot

A bot that helps schedule meetings by finding common free slots in participants' calendars.

## Setup

1. Create a `.env` file with the following variables:
```env
BACKEND_URL=your_backend_url
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
```

2. Configure Google OAuth:
- Create a project in Google Cloud Console
- Enable Calendar API
- Create OAuth 2.0 credentials
- Add authorized redirect URIs
- Add authorized JavaScript origins

## Development

```bash
npm install
npm run dev
```

## Deployment

The application is automatically deployed to Fly.io when changes are pushed to the main branch.

Required environment variables must be set in your Fly.io dashboard.
