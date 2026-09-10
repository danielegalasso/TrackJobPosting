/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // The portal's deep emerald banner and the amber alert CTA.
        brand: {
          DEFAULT: '#0e744e',
          dark: '#0b5c3e',
          light: '#128f60',
        },
        alert: '#f59e0b',
      },
    },
  },
  plugins: [],
};
