---
title: PRISM 설계 개요
summary: 평가 잡을 YAML로 선언하고 러너가 디바이스에 배포·수집하는 구조
updated_at: 2026-09-22
tags: [design, framework]
---
# PRISM 설계 개요

PRISM은 평가 잡을 YAML로 선언하면 러너가 디바이스에 배포하고 결과를 수집하는 프레임워크다.
잡 정의와 결과는 /nfs/prism/jobs/ 아래에 쌓인다. 러너는 디바이스별 어댑터를 플러그인으로 로드한다.
