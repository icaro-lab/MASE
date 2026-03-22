import path from 'node:path';
import { defineConfig } from 'vite';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      lib: path.resolve(__dirname, 'src/lib'),
      components: path.resolve(__dirname, 'src/components')
    }
  },
  define: {
    'process.env': '{}'
  },
  server: {
    host: true,
    port: 3016,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true
      }
    }
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.js'
  }
});
