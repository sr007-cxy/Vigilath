import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  root: __dirname,
  plugins: [react()],
  css: { postcss: __dirname },
  build: { outDir: '../.uikit-dist', emptyOutDir: true },
});
