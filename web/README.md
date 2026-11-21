# Dashboard

React + TypeScript operator dashboard for the collections queue, built with Vite.

```bash
npm ci
npm run dev     # http://localhost:5173, proxies /api to the backend on :8000
npm run build   # writes dist/, served by the backend at /
```

- `src/api.ts`: typed client; money is integer paise end to end.
- `src/i18n.ts`: English, Hindi and Tamil labels. Case reasons come from the server in the chosen language.
- `src/components/`: people list, case drawer, approval dialog, assistant, list import.
- `src/styles.css`: design tokens for light and dark themes.
