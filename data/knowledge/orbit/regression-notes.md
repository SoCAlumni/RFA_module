---
title: 벤치마크 회귀 원인 메모
summary: INT4 하락은 attention 출력 스케일 클리핑이 주원인
updated_at: 2026-09-18
tags: [benchmark, regression]
---
# 회귀 원인 메모

INT4 하락은 attention 출력의 스케일 클리핑에서 대부분 발생했다. per-channel 스케일로 바꾸면
절반 정도 회복된다. 나머지는 QAT 없이는 어렵다는 게 현재 판단이다.
