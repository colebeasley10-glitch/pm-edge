# PM Edge Native App

This directory is the native iPhone/Android client for PM Edge.

## Architecture

- **Expo / React Native** for the native mobile UI.
- **mobile_backend.py** is the server-side API. It reads the existing PM Edge ledgers and system state.
- Kalshi private keys stay on the server. They are never embedded in the phone app.
- The native app opens the existing signed PM Edge approval flow for the final real-money Submit step.

## Required server environment

- `PM_EDGE_MOBILE_API_TOKEN` — bearer token used by the native client.
- Existing PM Edge / Streamlit configuration, including `STREAMLIT_APP_URL` and approval secret, so the API can mint Review & Place links.

Run locally:

```bash
uvicorn mobile_backend:app --reload --port 8000
```

## Native client

Copy `.env.example` to `.env`, set the API URL/token, then:

```bash
npm install
npx expo start
```

For TestFlight/App Store builds, initialize the Expo project with EAS and add the resulting project ID to `app.json`.

The native client intentionally contains no Kalshi write credential.
