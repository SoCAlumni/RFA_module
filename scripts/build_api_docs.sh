#!/usr/bin/env bash
# contracts/*.openapi.yaml 을 Redoc HTML 로 만든다. 결과는 ${OUT:-_site}/ (gitignore).
# 로컬 확인: ./scripts/build_api_docs.sh && python3 -m http.server -d _site 8800
set -euo pipefail

REDOCLY_VERSION=2.54.3
OUT=${OUT:-_site}
cd "$(dirname "$0")/.."
mkdir -p "$OUT"

links=""
for spec in contracts/*.openapi.yaml; do
  name=$(basename "$spec" .openapi.yaml)
  npx -y "@redocly/cli@${REDOCLY_VERSION}" build-docs "$spec" -o "$OUT/$name.html"
  title=$(sed -n 's/^  title: //p' "$spec" | head -1)
  links+="<li><a href=\"$name.html\">$title</a> <code>$spec</code></li>"
done

cat > "$OUT/index.html" <<HTML
<!doctype html>
<html lang="ko">
<meta charset="utf-8">
<title>RFA API contracts</title>
<body style="font-family: sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem">
<h1>RFA API contracts</h1>
<ul>$links</ul>
<p>main 의 contracts/ 에서 자동 생성. 수정은 yaml 로.</p>
</body>
</html>
HTML
