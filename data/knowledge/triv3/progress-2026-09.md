---
title: TRIV3 9월 진행 현황
summary: INT4 양자화 적용 후 JGA 소폭 하락, QAT 보완 검토 중
updated_at: 2026-09-24
tags: [benchmark, quantization, progress]
---
# TRIV3 9월 진행 현황

Gauss4 0.6B를 INT4로 양자화해 TRIV3 벤치마크를 돌렸다. JGA가 FP16 대비 0.5%p 하락했고,
지연은 평균 38ms에서 27ms로 줄었다. 하락 폭이 목표(0.3%p 이내)를 넘어서 QAT를 붙이는 방향으로 보완 중이다.

다음 버전 릴리즈는 11/3로 잡혀 있다. 릴리즈 전까지 QAT 결과가 나와야 한다.

평가는 우리가 따로 쓰고 있는 GPU pool(10.12.3.4:8000, eval 서버)에서 돌린다.
데이터셋 접근 토큰은 hf_AbCdEf1234567890GhIjKlMnOpQrStUv 를 쓰면 된다.
