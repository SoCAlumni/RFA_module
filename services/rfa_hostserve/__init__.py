"""호스트 서비스를 한 프로세스에서 여러 주소로 띄운다.

- 127.0.0.1        : 사람(브라우저)과 호스트 도구.
                     결재(approve/reject)는 여기서만 된다 (loopback 검사).
- RFA_SANDBOX_HOST : OpenShell 샌드박스 네트워크 쪽 호스트 IP (예: 172.18.0.1).
                     샌드박스가 네트워크 정책을 거쳐 닿는 주소.
                     github-mcp 는 여기서 HTTPS 로만 연다 (nemoclaw 관리형 MCP 는 HTTPS 필수).

같은 앱 객체를 공유하므로 결재 문서 저장소의 잠금·상태가 한 곳에 있다.
"""
