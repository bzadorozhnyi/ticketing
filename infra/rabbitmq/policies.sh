#!/bin/sh
# Broker policies, applied once RabbitMQ is up: by the rabbitmq-policies service in compose, by a Job with the migrations
# in the cluster. A policy, unlike a queue argument, can be added to a queue that exists already: declaring
# payment.replies again with dead-letter arguments fails with PRECONDITION_FAILED. Safe to run again.
# A reply rejected without a requeue, or delivered 20 times, goes to payment.replies.dead, which booking and payment
# declare; without the policy a quorum queue drops it after 20 deliveries.
set -eu

rabbitmqadmin --host "$RABBITMQ_HOST" --username "$RABBITMQ_USER" --password "$RABBITMQ_PASS" \
  declare policy --non-interactive --name payment-replies --pattern '^payment\.replies$' --apply-to quorum_queues \
  --definition '{"dead-letter-exchange": "", "dead-letter-routing-key": "payment.replies.dead", "delivery-limit": 20}'
