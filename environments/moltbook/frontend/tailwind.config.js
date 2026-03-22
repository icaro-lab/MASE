/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{html,js,svelte,ts}'],
  theme: {
    extend: {
      colors: {
        'molt': {
          50: '#fff5f0',
          100: '#ffe6db',
          200: '#ffc9b3',
          300: '#ffa080',
          400: '#ff6d4d',
          500: '#ff4500',
          600: '#e63d00',
          700: '#cc3600',
          800: '#a32c00',
          900: '#7a2100',
        },
        'dark': {
          100: '#1a1a2e',
          200: '#16162a',
          300: '#121226',
          400: '#0e0e22',
          500: '#0a0a1e',
          600: '#06061a',
          700: '#020216',
          800: '#000012',
          900: '#00000e',
        }
      }
    }
  },
  plugins: []
};
