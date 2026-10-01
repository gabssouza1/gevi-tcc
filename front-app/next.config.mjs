/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: gera HTML/CSS/JS estáticos em ``out/`` para hospedar no
  // S3 (prefixo ``frontend/``) servido pelo CloudFront (design.md).
  output: "export",
  // Sem otimização de imagem em runtime (incompatível com export estático);
  // as imagens são servidas como estão.
  images: { unoptimized: true },
  // Gera caminhos com barra final (index.html por rota) — amigável ao S3/OAC.
  trailingSlash: true,
};

export default nextConfig;
