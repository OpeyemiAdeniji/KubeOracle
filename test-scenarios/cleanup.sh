#!/bin/bash
# Remove all test scenario workloads

kubectl delete deployment \
  crashloop-test \
  imagepull-test \
  oomkilled-test \
  selector-test \
  probe-test \
  pending-test \
  --ignore-not-found

kubectl delete service selector-test-svc --ignore-not-found

echo "All test scenarios cleaned up."
