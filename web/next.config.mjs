/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    return [{
      source: "/fonts/Formula1-Bold.woff2",
      destination: "https://www.formula1.com/s/fonts/formula1/Formula1-Bold.woff2",
    }];
  },
};

export default nextConfig;
