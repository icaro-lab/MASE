import { handler } from './build/handler.js';
import express from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';

const app = express();
const backendTarget = process.env.BACKEND_URL || 'http://backend:8000';
const backendProxy = createProxyMiddleware({
  target: backendTarget,
  changeOrigin: true,
});

// Proxy API requests to backend
app.use('/api', backendProxy);

// Proxy root platform metrics endpoint to backend
app.use('/metrics', backendProxy);

// Proxy platform compatibility endpoints used directly by the compass page.
for (const path of [
  '/compass/status',
  '/compass/instrument',
  '/compass/review',
  '/compass/history',
  '/compass/submit',
  '/contract',
  '/health',
  '/journal',
  '/skill.md',
]) {
  app.use(path, backendProxy);
}

// Handle everything else with SvelteKit
app.use(handler);

const port = process.env.PORT || 3000;
app.listen(port, '0.0.0.0', () => {
  console.log(`Listening on http://0.0.0.0:${port}`);
});
