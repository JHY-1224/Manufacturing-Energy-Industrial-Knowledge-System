import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const backendOrigin = process.env.RAG_BACKEND_ORIGIN || 'http://127.0.0.1:8000';
const backendURL = new URL(backendOrigin);
if (backendURL.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(backendURL.hostname) || backendURL.username || backendURL.password || backendURL.pathname !== '/' || backendURL.search || backendURL.hash) {
  throw new Error('RAG_BACKEND_ORIGIN must be a localhost HTTP origin');
}

export default defineConfig({
  root: 'frontend',
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 8080,
    strictPort: true,
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
});
