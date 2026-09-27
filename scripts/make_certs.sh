#!/usr/bin/env bash
# 샌드박스 → 호스트 github MCP(HTTPS)용 로컬 CA 와 서버 인증서를 만든다. 결과는 certs/ (gitignore).
#   certs/rfa-ca.pem        CA 인증서  → onboard 때 NEMOCLAW_CORPORATE_CA_BUNDLE 로 샌드박스에 신뢰시킨다
#   certs/rfa-ca.key        CA 키      → 호스트에만. 샌드박스에 넣지 말 것
#   certs/host.pem/.key     서버 인증서 (SAN = IP:$RFA_SANDBOX_HOST) → rfa_hostserve 의 RFA_TLS_CERT/KEY
# 이미 있으면 CA 는 재사용하고(샌드박스가 이미 신뢰 중일 수 있으므로) 서버 인증서만 다시 만든다.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST_IP=${RFA_SANDBOX_HOST:?RFA_SANDBOX_HOST 가 필요해요 (예: 172.18.0.1)}
DIR=${CERT_DIR:-certs}
mkdir -p "$DIR"
chmod 700 "$DIR"

if [ ! -f "$DIR/rfa-ca.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 90 \
    -keyout "$DIR/rfa-ca.key" -out "$DIR/rfa-ca.pem" \
    -subj "/CN=RFA local sandbox CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
  echo "CA 생성: $DIR/rfa-ca.pem"
fi

openssl req -newkey rsa:2048 -nodes -keyout "$DIR/host.key" -out "$DIR/host.csr" \
  -subj "/CN=$HOST_IP" 2>/dev/null
openssl x509 -req -in "$DIR/host.csr" -CA "$DIR/rfa-ca.pem" -CAkey "$DIR/rfa-ca.key" \
  -CAcreateserial -days 90 -out "$DIR/host.pem" \
  -extfile <(printf "subjectAltName=IP:%s\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n" "$HOST_IP") 2>/dev/null
rm -f "$DIR/host.csr"
chmod 600 "$DIR"/*.key
# nemoclaw 는 그룹·기타 쓰기가 가능한 CA 파일을 거부한다 (umask 002 환경 주의)
chmod 644 "$DIR/rfa-ca.pem" "$DIR/host.pem"
echo "서버 인증서: $DIR/host.pem (SAN IP:$HOST_IP)"
