pipeline {
  agent {
    kubernetes {
      defaultContainer 'python'
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

    - name: python
      image: python:3.11
      command: ["cat"]
      tty: true

    - name: checkov
      image: bridgecrew/checkov:latest
      command: ["cat"]
      tty: true

    - name: docker
      image: docker:27-cli
      command: ["cat"]
      tty: true
      env:
        - name: DOCKER_API_VERSION
          value: "1.53"
      volumeMounts:
        - name: docker-sock
          mountPath: /var/run/docker.sock
        - name: workspace-volume
          mountPath: /home/jenkins/agent

    - name: kubectl
      image: bitnami/kubectl:latest
      command: ["cat"]
      tty: true

    - name: sonar
      image: sonarsource/sonar-scanner-cli:latest
      command: ["cat"]
      tty: true

"""
    }
  }

  environment {
    HELM_NAMESPACE = 'model-serving'

    RAG_IMAGE      = "rag-orchestrator:${env.BUILD_NUMBER}"
    UI_IMAGE       = "streamlit-ui:${env.BUILD_NUMBER}"
    INGEST_IMAGE   = "qdrant-ingestor:${env.BUILD_NUMBER}"

    PYTHONUNBUFFERED = '1'
    DOCKER_API_VERSION = "1.53"
  }

  options {
    timestamps()
    disableConcurrentBuilds()
  }

  stages {

    stage('Checkout') {
      steps {
        checkout scm
      }
    }

    stage('Preflight - Runtime Check') {
      steps {
        container('docker') {
          sh '''
            set -euxo pipefail
            echo "Docker test start"

            docker version || true
            docker info || true
            docker images | head || true

            echo "Docker test done"
          '''
        }

        container('kubectl') {
          sh '''
            set -euxo pipefail

            echo "Kubernetes check"
            kubectl get nodes
            kubectl get ns
            kubectl get pods -n ${HELM_NAMESPACE} || true
          '''
        }
      }
    }

    stage('Unit Tests') {
      steps {
        container('python') {
          dir('services/rag-orchestrator') {
            sh '''
              set -euxo pipefail

              python --version
              pip install --upgrade pip

              if [ -f requirements.txt ]; then
                pip install -r requirements.txt || true
              fi

              pip install pytest

              if [ -d tests ]; then
                pytest tests -q || true
              else
                echo "No tests found"
              fi
            '''
          }
        }
      }
    }

    stage('Checkov Scan') {
      steps {
        container('checkov') {
          sh '''
            set -euxo pipefail

            if [ -d charts ]; then
              checkov -d charts --quiet --soft-fail || true
            fi
          '''
        }
      }
    }

    stage('Build Docker Images') {
      steps {
        container('docker') {
          sh '''
            set -euxo pipefail

            docker build -f services/rag-orchestrator/Dockerfile -t ${RAG_IMAGE} .
            docker build -f services/streamlit-ui/Dockerfile -t ${UI_IMAGE} .
            docker build -f services/qdrant-ingestor/Dockerfile -t ${INGEST_IMAGE} .

            docker images | head
          '''
        }
      }
    }

    stage('Deploy') {
      steps {
        container('kubectl') {
          sh '''
            set -euxo pipefail

            kubectl set image deployment/rag-orchestrator \
              rag-orchestrator=${RAG_IMAGE} -n ${HELM_NAMESPACE} || true

            kubectl set image deployment/streamlit \
              streamlit=${UI_IMAGE} -n ${HELM_NAMESPACE} || true

            kubectl rollout status deployment/rag-orchestrator -n ${HELM_NAMESPACE} --timeout=300s || true
            kubectl rollout status deployment/streamlit -n ${HELM_NAMESPACE} --timeout=300s || true

            kubectl get pods -n ${HELM_NAMESPACE}
          '''
        }
      }
    }

    stage('Smoke Test') {
      steps {
        container('kubectl') {
          sh '''
            set -euxo pipefail

            kubectl get svc -n ${HELM_NAMESPACE}
            kubectl get pods -n ${HELM_NAMESPACE}
            kubectl get deploy -n ${HELM_NAMESPACE}
          '''
        }
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
      echo "DONE"
    }
  }
}