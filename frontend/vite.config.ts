import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { visualizer } from 'rollup-plugin-visualizer'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  cacheDir: '.vite/build-cache',
  build: {
    outDir: 'dist',
    sourcemap: false,
    minify: 'terser',
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom')) {
            return 'vendor';
          }
          if (id.includes('node_modules/react-router-dom')) {
            return 'router';
          }
          if (id.includes('node_modules/i18next') || id.includes('node_modules/react-i18next')) {
            return 'i18n';
          }
          if (id.includes('node_modules/@radix-ui/react-tooltip')) {
            return 'ui';
          }
        }
      },
      plugins: [
        // Analysis artifact lives outside dist/ so it never ships to webroot.
        // open:false — on headless/EC2 build env, opening a browser hangs.
        // Run `npx vite build && xdg-open stats.html` locally to view.
        visualizer({
          open: false,
          filename: 'stats.html'
        })
      ]
    }
  },
  server: {
    port: 3000,
    open: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8070',
        changeOrigin: true,
      },
      '/pay': {
        target: 'http://localhost:3010',
        changeOrigin: true,
      },
    },
  }
})
