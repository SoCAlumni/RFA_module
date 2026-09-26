---
title: QAT 방법론 정리
summary: LSQ, PACT, 그리고 최근 QAT 논문 요약
updated_at: 2026-09-20
tags: [qat, survey]
---
# QAT 방법론 정리

LSQ(Learned Step Size Quantization)는 스케일을 학습 가능한 파라미터로 두어 양자화 오차를 줄인다.
PACT는 활성값 클리핑 임계값을 학습한다. 두 방법 모두 공개 논문이며 구현체가 공개되어 있다.
소규모 모델에서는 QAT 1~2 epoch 만으로 PTQ 대비 상당한 회복이 보고되어 있다.
