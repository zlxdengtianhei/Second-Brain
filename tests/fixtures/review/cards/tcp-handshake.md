---
id: ex_handshake
title: TCP handshake
kind: mechanism
domain: net
tags: [review, flashcards/net]
relations:
  - type: follows
    target: ex_tcp
status: confirmed
pillar: Transport
topic: Connection setup
---
## 本质
TCP opens a connection with three messages (SYN, SYN-ACK, ACK) so both sides agree on initial sequence numbers.

## 复习卡
TCP 三次握手依次发送哪三个报文？::SYN、SYN-ACK、ACK。
为什么需要第三次握手？::让服务器确认客户端收到了它的初始序号。
