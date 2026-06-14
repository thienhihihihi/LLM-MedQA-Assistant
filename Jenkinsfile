pipeline {

  agent {
    kubernetes {
      defaultContainer 'ci'
      yaml """
apiVersion: v1
kind: Pod
spec:
  serviceAccountName: jenkins

  volumes:
    - name: docker-sock
      hostPath:
        path: /var/run/docker.sock

    - name: workspace-volume
      emptyDir: {}

  containers:
    - name: ci
      image: python:3.11-slim
      command: ["cat"]
      tty: true
      env:
        - name: DOCKER_API_VERSION
          value: "1.53"
        - name: PYTHONUNBUFFERED
          value: "1"
      volumeMounts:
        - name: docker-sock
          mountPath: /var/run/docker.sock
        - name: workspace-volume
          mountPath: /home/jenkins/agent
"""
    }
  }

  environment {
    HELM_NAMESPACE = "model-serving"

    RAG_IMAGE    = "rag-orchestrator:${BUILD_NUMBER}"
    UI_IMAGE     = "streamlit-ui:${BUILD_NUMBER}"
    INGEST_IMAGE = "qdrant-ingestor:${BUILD_NUMBER}"

    DOCKER_API_VERSION = "1.53"
    PYTHONUNBUFFERED = "1"
  }

  options {
    timestamps()
    disableConcurrentBuilds()
    skipDefaultCheckout()
  }

  stages {

    stage('Checkout') {
      steps {
        checkout scm
      }
    }

    stage('Install CI Tools') {
      steps {
        sh '''#!/bin/sh
          set -eux

          echo "=== Install base tools ==="
          apt-get update
          apt-get install -y --no-install-recommends \
            ca-certificates \
            curl \
            gnupg \
            git \
            bash

          echo "=== Install Docker CLI 27 ==="
          install -m 0755 -d /etc/apt/keyrings
          curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
          chmod a+r /etc/apt/keyrings/docker.asc

          echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list

          apt-get update
          apt-get install -y --no-install-recommends docker-ce-cli

          echo "=== Install kubectl ==="
          curl -L -o /usr/local/bin/kubectl https://dl.k8s.io/release/v1.30.0/bin/linux/amd64/kubectl
          chmod +x /usr/local/bin/kubectl

          echo "=== Install Python CI packages ==="
          python -m pip install --upgrade pip setuptools wheel
          pip install pytest pytest-cov checkov

          echo "=== Verify tools ==="
          python --version
          pip --version
          docker version
          kubectl version --client
          checkov --version || true
        '''
      }
    }

    stage('Preflight Check') {
      steps {
        sh '''#!/bin/sh
          set -eux

          echo "=== Docker check ==="
          docker version
          docker info

          echo "=== Kubernetes namespace check ==="
          kubectl get pods -n "$HELM_NAMESPACE"
          kubectl get deploy -n "$HELM_NAMESPACE"
          kubectl get svc -n "$HELM_NAMESPACE"
        '''
      }
    }

    stage('Unit Tests') {
      steps {
        dir('services/rag-orchestrator') {
          sh '''#!/bin/sh
            set -eux

            echo "=== Python test environment ==="
            python --version
            pip --version

            if [ -f requirements.txt ]; then
              pip install -r requirements.txt || true
            fi

            if [ -d tests ]; then
              pytest tests -q || true
            else
              echo "No tests found"
            fi
          '''
        }
      }
    }

    stage('Security Scan') {
      steps {
        sh '''#!/bin/sh
          set -eux

          if [ -d charts ]; then
            checkov -d charts --soft-fail || true
          else
            echo "No charts directory"
          fi
        '''
      }
    }

    stage('Build Docker Images') {
      steps {
        sh '''#!/bin/sh
          set -eux

          docker build -f services/rag-orchestrator/Dockerfile -t "$RAG_IMAGE" .
          docker build -f services/streamlit-ui/Dockerfile -t "$UI_IMAGE" .
          docker build -f services/qdrant-ingestor/Dockerfile -t "$INGEST_IMAGE" .

          docker images | grep -E "rag-orchestrator|streamlit-ui|qdrant-ingestor" || true
        '''
      }
    }

    stage('Deploy to Kubernetes') {
      steps {
        sh '''#!/bin/sh
          set -eux

          kubectl set image deployment/rag-orchestrator \
            rag-orchestrator="$RAG_IMAGE" \
            -n "$HELM_NAMESPACE"

          kubectl patch deployment rag-orchestrator \
            -n "$HELM_NAMESPACE" \
            -p '{"spec":{"template":{"spec":{"containers":[{"name":"rag-orchestrator","imagePullPolicy":"IfNotPresent"}]}}}}'

          kubectl set image deployment/streamlit \
            streamlit="$UI_IMAGE" \
            -n "$HELM_NAMESPACE"

          kubectl patch deployment streamlit \
            -n "$HELM_NAMESPACE" \
            -p '{"spec":{"template":{"spec":{"containers":[{"name":"streamlit","imagePullPolicy":"IfNotPresent"}]}}}}'

          kubectl rollout status deployment/rag-orchestrator -n "$HELM_NAMESPACE" --timeout=300s
          kubectl rollout status deployment/streamlit -n "$HELM_NAMESPACE" --timeout=300s
        '''
      }
    }

    stage('Smoke Test') {
      steps {
        sh '''#!/bin/sh
          set -eux

          kubectl get pods -n "$HELM_NAMESPACE" -o wide
          kubectl get svc -n "$HELM_NAMESPACE"
          kubectl get deploy -n "$HELM_NAMESPACE"
        '''
      }
    }
  }

  post {
    success {
      echo "PIPELINE SUCCESS"
    }

    failure {
      echo "PIPELINE FAILED - CHECK LOGS"
    }

    always {
      echo "PIPELINE FINISHED"
    }
  }
}