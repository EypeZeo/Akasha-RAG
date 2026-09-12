import tailwindcssAnimate from 'tailwindcss-animate'

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        warm: {
          bg: '#FAF3E8',
          panel: '#FFFBF5',
          50: '#FDF8F2',
          100: '#F5EDE0',
          200: '#E8DDD0',
        },
        accent: {
          DEFAULT: '#E8594A',
          hover: '#D04A3C',
          light: 'rgba(232,89,74,0.12)',
        },
        amber: {
          DEFAULT: '#D4943A',
          light: 'rgba(212,148,58,0.12)',
        },
        ink: {
          DEFAULT: '#2C2416',
          soft: '#5A4F3F',
          muted: '#8B7E6A',
        },
        success: {
          DEFAULT: '#5B8C5A',
          light: 'rgba(91,140,90,0.12)',
        },
      },
      fontFamily: {
        body: ['"Inter"', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', '"Noto Sans SC"', '"PingFang SC"', '"Microsoft YaHei"', '"Segoe UI Emoji"', '"Apple Color Emoji"', 'sans-serif'],
        display: ['"ZCOOL XiaoWei"', '"Songti SC"', 'serif'],
      },
    },
  },
  plugins: [tailwindcssAnimate],
}
